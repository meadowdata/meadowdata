from __future__ import annotations

import abc
import dataclasses
import functools
import random
import uuid
from dataclasses import dataclass
from typing import (
    Final,
    Iterable,
    Sequence,
    List,
    Any,
    Dict,
    Optional,
    Union,
    Literal,
    Callable,
)

from meadowflow.event_log import EventLog, Event, Timestamp
import meadowflow.topic
import meadowflow.effects
import meadowflow.events_arg
from meadowflow.scopes import ScopeValues, BASE_SCOPE, ALL_SCOPES
from meadowflow.topic_names import TopicName, FrozenDict, CURRENT_JOB
from meadowrun.deployed_function import (
    MeadowRunDeployedFunction,
    MeadowRunDeployedCommand,
)

JobState = Literal[
    # Nothing is currently happening with the job
    "WAITING",
    # A run of the job has been requested on a job runner
    "RUN_REQUESTED",
    # The job is currently running. JobPayload.pid will be populated
    "RUNNING",
    # The job has completed normally. JobPayload.result_value and pid will be populated
    "SUCCEEDED",
    # The job was cancelled by the user. JobPayload.pid will be populated
    "CANCELLED",
    # The job failed. JobPayload.failure_type and pid will be populated. If failure_type
    # is PYTHON_EXCEPTION or RUN_REQUEST_FAILED, raised_exception will be populated, if
    # failure_type is NON_ZERO_RETURN_CODE, return_code will be populated
    "FAILED",
]


@dataclass(frozen=True)
class RaisedException:
    """Represents a python exception raised by a remote process"""

    exception_type: str
    exception_message: str
    exception_traceback: str


@dataclass(frozen=True)
class JobPayload:
    """The Event.payload for Job-related events. See JobStateType docstring."""

    request_id: Optional[str]
    state: JobState
    failure_type: Optional[
        Literal["PYTHON_EXCEPTION", "NON_ZERO_RETURN_CODE", "RUN_REQUEST_FAILED"]
    ] = None
    pid: Optional[int] = None
    result_value: Any = None
    effects: Optional[meadowflow.effects.Effects] = None
    raised_exception: Union[RaisedException, BaseException, None] = None
    return_code: Optional[int] = None


@dataclasses.dataclass(frozen=True)
class LocalFunction:
    """
    A function pointer in the current codebase with arguments for calling the
    function
    """

    function_pointer: Callable[..., Any]
    function_args: Sequence[Any] = dataclasses.field(default_factory=lambda: [])
    function_kwargs: Dict[str, Any] = dataclasses.field(default_factory=lambda: {})


JobRunnerFunctionTypes = (
    LocalFunction,
    MeadowRunDeployedCommand,
    MeadowRunDeployedFunction,
)
# A JobRunnerFunction is a function/executable/script that one or more JobRunners will
# know how to run along with the arguments for that function/executable/script
JobRunnerFunction = Union[JobRunnerFunctionTypes]


class JobRunner(abc.ABC):
    """An interface for job runner clients"""

    @abc.abstractmethod
    async def run(
        self,
        job_name: TopicName,
        run_request_id: str,
        job_runner_function: JobRunnerFunction,
    ) -> None:
        pass

    @abc.abstractmethod
    async def poll_jobs(self, last_events: Iterable[Event[JobPayload]]) -> None:
        """
        last_events is the last event we've recorded for the jobs that we are interested
        in. poll_jobs will add new events to the EventLog for these jobs if there's been
        any change in their state.
        """
        pass

    @abc.abstractmethod
    def can_run_function(self, job_runner_function: JobRunnerFunction) -> bool:
        """Is this JobRunner compatible with the specified job_runner_function"""
        pass


class VersionedJobRunnerFunction(abc.ABC):
    """
    Similar to a JobRunnerFunction, but instead of a single version of the code (e.g. a
    specific commit in a git repo), specifies a versioned codebase (e.g. a git repo),
    along with a function/executable/script in that repo (and also including the
    arguments to call that function/executable/script).

    TODO this is not yet fully fleshed out and the interface will probably need to
     change
    """

    @abc.abstractmethod
    def get_job_runner_function(self) -> JobRunnerFunction:
        pass


class JobRunnerPredicate(abc.ABC):
    """JobRunnerPredicates specify which job runners a job can run on"""

    @abc.abstractmethod
    def apply(self, job_runner: JobRunner) -> bool:
        pass


JobFunction = Union[JobRunnerFunction, VersionedJobRunnerFunction]


@dataclass
class Job(meadowflow.topic.Topic):
    """
    A job runs python code (specified job_run_spec) on a job_runner. The scheduler will
    also perform actions automatically based on trigger_actions.
    """

    # these fields should be frozen

    # job_function specifies "where is the codebase and interpreter" (called a
    # "deployment" in meadowrun), "how do we invoke the function/executable/script for
    # this job" (e.g. MeadowRunFunction), and "what are the arguments for that
    # function/executable/script". This can be a JobRunnerFunction, which is something
    # that at least one job runner will know how to run, or a
    # "VersionedJobRunnerFunction" (currently the only implementation is
    # MeadowRunFunctionGitRepo) which is something that can produce different versions
    # of a JobRunnerFunction
    job_function: JobFunction

    # specifies what actions to take when
    trigger_actions: Sequence[meadowflow.topic.TriggerAction]

    # specifies which job runners this job can run on
    job_runner_predicate: Optional[JobRunnerPredicate] = None

    # specifies the scope that this job is part of, defaults to the BASE_SCOPE
    scope: ScopeValues = BASE_SCOPE

    # these fields are computed by the Scheduler

    # all topic_names that trigger_actions are dependent on
    all_subscribed_topics: Optional[Sequence[TopicName]] = None

    def __post_init__(self) -> None:
        if self.scope == ALL_SCOPES:
            raise ValueError("Job.scope cannot be set to ALL_SCOPES")

        if self.name == CURRENT_JOB:
            raise ValueError("Job.name cannot be set to CURRENT_JOB")


@dataclass
class JobRunOverrides:
    """
    This class specifies overrides for a manual run of a job. Different fields will
    apply to different types of jobs.
    """

    function_args: Optional[Sequence[Any]] = None
    function_kwargs: Optional[Dict[str, Any]] = None
    context_variables: Optional[Dict[str, Any]] = None

    # Equivalent to meadowdb.connection.set_default_userspace
    meadowdb_userspace: Optional[str] = None

    # TODO add things like branch/commit override for git-based deployments


def _apply_job_run_overrides(
    run_overrides: JobRunOverrides, job_runner_function: JobRunnerFunction
) -> JobRunnerFunction:
    """Applies run_overrides to job_runner_function"""
    if run_overrides is not None:
        if run_overrides.function_args or run_overrides.function_kwargs:
            to_replace = {}
            if run_overrides.function_args:
                to_replace["function_args"] = run_overrides.function_args
            if run_overrides.function_kwargs:
                to_replace["function_kwargs"] = run_overrides.function_kwargs

            if isinstance(job_runner_function, LocalFunction):
                job_runner_function = dataclasses.replace(
                    job_runner_function, **to_replace
                )
            elif isinstance(job_runner_function, MeadowRunDeployedFunction):
                job_runner_function = dataclasses.replace(
                    job_runner_function,
                    meadowrun_function=dataclasses.replace(
                        job_runner_function.meadowrun_function, **to_replace
                    ),
                )
            else:
                raise ValueError(
                    "run_overrides specified function_args/function_kwargs but "
                    f"job_runner_function is of type {type(job_runner_function)}, "
                    "and we don't know how to apply function_args/function_kwargs "
                    "to that type of job_runner_function"
                )

        if run_overrides.context_variables:
            if isinstance(job_runner_function, MeadowRunDeployedCommand):
                job_runner_function = dataclasses.replace(
                    job_runner_function,
                    context_variables=run_overrides.context_variables,
                )
            else:
                raise ValueError(
                    "run_overrides specified context_variables but job_runner_function "
                    f"is of type {type(job_runner_function)} and we don't know how to "
                    "apply context_variables to that type of job_runner_function"
                )

        if run_overrides.meadowdb_userspace:
            if isinstance(
                job_runner_function,
                (MeadowRunDeployedCommand, MeadowRunDeployedFunction),
            ):
                # this needs to line up with
                # meadowdb.connection._MEADOWDB_DEFAULT_USERSPACE but we prefer not
                # taking the dependency here
                new_dict = {
                    "MEADOWDB_DEFAULT_USERSPACE": run_overrides.meadowdb_userspace
                }
                if job_runner_function.environment_variables:
                    job_runner_function.environment_variables.update(**new_dict)
                else:
                    job_runner_function = dataclasses.replace(
                        job_runner_function, environment_variables=new_dict
                    )
            else:
                raise ValueError(
                    "run_overrides specified meadowdb_userspace but job_runner_function"
                    f" is of type {type(job_runner_function)}, and we don't know how to"
                    "apply meadowdb_userspace to that type of job_runner_function"
                )

    return job_runner_function


@dataclass(frozen=True)
class Run(meadowflow.topic.Action):
    """Runs the job"""

    async def execute(
        self,
        job: Job,
        run_overrides: Optional[JobRunOverrides],
        available_job_runners: List[JobRunner],
        event_log: EventLog,
        timestamp: Timestamp,
    ) -> str:
        """
        Returns a request id. If the job is already requested or running, this will
        return the previous run request id (new run requests while the job is already
        requested/running are ignored)
        """
        ev: Event[JobPayload] = event_log.last_event(job.name, timestamp)
        # TODO not clear that this is the right behavior, vs queuing up another run once
        #  the current run is done.
        if ev is not None and ev.payload.state in ["RUN_REQUESTED", "RUNNING"]:
            # TODO maybe indicate somehow that this job request already existed?
            return ev.payload.request_id
        else:
            run_request_id = str(uuid.uuid4())

            # convert a job_function into a job_runner_function
            if isinstance(job.job_function, VersionedJobRunnerFunction):
                job_runner_function = job.job_function.get_job_runner_function()
            elif isinstance(job.job_function, JobRunnerFunctionTypes):
                job_runner_function = job.job_function
            else:
                raise ValueError(
                    "job_run_spec is neither VersionedJobRunnerFunction nor a "
                    f"JobRunnerFunction, instead is a {type(job.job_function)}"
                )

            # Apply any JobRunOverrides
            job_runner_function = _apply_job_run_overrides(
                run_overrides, job_runner_function
            )

            # replace any LatestEventArgs
            job_runner_function = meadowflow.events_arg.replace_latest_events(
                job_runner_function, job, event_log, timestamp
            )

            # choose a job runner and run
            await choose_job_runner(
                job, job_runner_function, available_job_runners
            ).run(job.name, run_request_id, job_runner_function)

            return run_request_id


def choose_job_runner(
    job: Job, job_runner_function: JobRunnerFunction, job_runners: List[JobRunner]
) -> JobRunner:
    """
    Chooses a job_runner that is compatible with job.

    TODO this logic should be much more sophisticated, look at available resources, etc.
    """
    if job.job_runner_predicate is None:
        compatible_job_runners = job_runners
    else:
        compatible_job_runners = [
            jr for jr in job_runners if job.job_runner_predicate.apply(jr)
        ]
    compatible_job_runners = [
        jr for jr in compatible_job_runners if jr.can_run_function(job_runner_function)
    ]

    if len(compatible_job_runners) == 0:
        # TODO this should probably get sent to the event log somehow. Also, what if we
        #  don't currently have any job runners that satisfy the predicates but one
        #  shows up in the near future?
        raise ValueError(
            f"No job runners were found that satisfy the predicates for {job.name} and "
            f"are compatible with {type(job_runner_function)}"
        )
    else:
        return random.choice(compatible_job_runners)


class Actions:
    """All the available actions"""

    run: Final[Run] = Run()
    # TODO other actions: abort, bypass, init, pause


@dataclass(frozen=True)
class AnyJobStateEventFilter(meadowflow.topic.EventFilter):
    """Triggers when any of job_names is in any of on_states"""

    job_names: Sequence[TopicName]
    on_states: Sequence[JobState]

    def topic_names_to_subscribe(self) -> Iterable[TopicName]:
        yield from self.job_names

    def apply(self, event: Event) -> bool:
        return event.payload.state in self.on_states


@dataclass(frozen=True)
class AllJobStatePredicate(meadowflow.topic.StatePredicate):
    """
    Condition is met when all of job_names are in one of on_states. job_names can
    include CURRENT_JOB
    """

    job_names: Sequence[TopicName]
    on_states: Sequence[JobState]

    def topic_names_to_query(self) -> Iterable[TopicName]:
        yield from self.job_names

    def apply(
        self,
        event_log: EventLog,
        low_timestamp: Timestamp,
        high_timestamp: Timestamp,
        current_job_name: TopicName,
    ) -> bool:
        for job_name in self.job_names:
            # support CURRENT_JOB placeholder
            if job_name == CURRENT_JOB:
                job_name = current_job_name

            # Make sure the most recent event is in the specified state. Technically,
            # the latest_event is None check should not be required because jobs always
            # get created in the "WAITING" state, but there's no reason to take that
            # assumption here.
            latest_event = event_log.last_event(job_name, high_timestamp)
            if latest_event is None or latest_event.payload.state not in self.on_states:
                return False

        return True


# this really belongs in scopes.py but the circular dependencies make it hard to do that
def add_scope_jobs_decorator(
    func: Callable[[ScopeValues, ...], Sequence[Job]]
) -> Callable[[FrozenDict[TopicName, Optional[Event]], ...], Sequence[Job]]:
    """
    A little bit of boilerplate to make it easier to write functions that create jobs in
    a specific scope, which is a common use case.

    Example:
        @add_scope_jobs_decorator
        def add_scope_jobs(scope: ScopeValues, arg1: Any, ...) -> Sequence[Job]:
            return [
                # use e.g. scope["date"] or scope["userspace"] in the job definition
                Job(pname("my_job1"), ...),
                Job(pname("my_job2"), ...)
            ]

    This function can then be scheduled like:
        Job(
            pname("add_date_scope_jobs"),
            Function(_run_add_scope_jobs, [LatestEventsArg.construct()]),
            [
                TriggerAction(
                    Actions.run, [ScopeInstantiated(frozenset("date", "userspace"))]
                )
            ]
        )

    This function takes care of two bits of boilerplate:
    1. At the start of the function, converts a FrozenDict[TopicName, Optional[Event]],
       (which is what LatestEventsArg gives us) into ScopeValues, which is what we can
       actually use
    2. At the end of the function, append all of the scope key/value pairs to the names
       of all of the jobs created in func. If you don't do this, then every instance of
       the scope will create identical jobs which is not what you want. Also, this means
       that you cannot have job names that include keys that are the same as the scope.
    """

    # this functools.wraps is more important than it seems--functions that are not
    # decorated using this pattern cannot be pickled
    @functools.wraps(func)
    def wrapper(
        events: FrozenDict[TopicName, Optional[Event]], *args, **kwargs
    ) -> Sequence[Job]:
        # find the instantiate scope event:
        scopes = [
            e.payload for e in events.values() if isinstance(e.payload, ScopeValues)
        ]
        if len(scopes) != 1:
            raise ValueError(
                "the adds_scope_jobs decorator must be used on a function that depends "
                "exactly one ScopeInstantiated topic. This function was called with "
                f"{len(scopes)} scopes"
            )
        scope = scopes[0]

        # now call the wrapped function
        jobs_to_add = func(scope, *args, **kwargs)

        # now adjust the names of the returned jobs
        for job in jobs_to_add:
            name = job.name.as_mutable()
            for key, value in scope.items():
                if key in name:
                    raise ValueError(
                        f"Cannot create job {job.name} in scope because both job name "
                        f"and scope have a {key} key"
                    )
                name[key] = value
            job.scope = scope
            job.name = TopicName(name)

        return jobs_to_add

    return wrapper
