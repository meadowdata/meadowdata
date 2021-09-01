import asyncio
import threading
import traceback
from typing import Dict, List, Tuple, Iterable, Callable

from nextbeat.event_log import Event, EventLog, Timestamp, AppendEventType
from nextbeat.jobs import Actions, Job
from nextbeat.jobs_common import JobPayload, JobRunner
from nextbeat.topic import Action, Topic


class Scheduler:
    """
    A scheduler gets set up with jobs, and then executes actions on jobs as per the
    triggers defined on those jobs.

    TODO there are a lot of weird assumptions about what's called "on the event loop" vs
     from outside of it/on a different thread and what's threadsafe
    """

    _JOB_RUNNER_POLL_DELAY_SECONDS: float = 1

    def __init__(
        self,
        # TODO the API is a bit confusing right now--Jobs specify a job_runner, but so
        #  do Schedulers.
        job_runner_constructor: Callable[[AppendEventType], JobRunner],
        job_runner_poll_delay_seconds: float = _JOB_RUNNER_POLL_DELAY_SECONDS,
    ) -> None:
        self._job_runner_poll_delay_seconds = job_runner_poll_delay_seconds

        self._event_loop = asyncio.new_event_loop()
        # the NextRunJobRunner uses gRPC.aio, which just grabs the current event_loop
        asyncio.set_event_loop(self._event_loop)
        self._event_log = EventLog(self._event_loop)
        self._jobs: Dict[str, Job] = {}
        # the list of jobs that we've added but haven't created subscriptions for yet
        self._create_job_subscriptions_queue: List[Job] = []
        self._job_runner = job_runner_constructor(self._event_log.append_event)

    def add_job(self, job: Job) -> None:
        """
        Note that create_job_subscriptions needs to be called separately (see
        docstring).
        """
        if job.name in self._jobs:
            raise ValueError(f"Job with name {job.name} already exists.")
        self._jobs[job.name] = job
        self._create_job_subscriptions_queue.append(job)
        self._event_log.append_event(job.name, JobPayload(None, "WAITING"))

    def create_job_subscriptions(self) -> None:
        """
        Should be called after all jobs are added.

        Adding jobs and creating subscriptions is done in two phases to avoid order
        dependence (otherwise can't add a job that triggers based on another without
        adding the other first), and allows even circular dependencies. I.e. add_job
        should be called (repeatedly), then create_job_subscriptions should be called.
        """

        # TODO: this should also check the new jobs' preconditions against the existing
        #  state. Perhaps they should already trigger.
        # TODO: should make sure we don't try to proceed without calling
        #  create_job_subscriptions first
        for job in self._create_job_subscriptions_queue:
            for trigger, action in job.trigger_actions:

                async def subscriber(
                    low_timestamp: Timestamp, high_timestamp: Timestamp
                ) -> None:
                    events: Dict[str, Tuple[Event, ...]] = {}
                    for name in trigger.topic_names_to_subscribe():
                        events[name] = tuple(
                            self._event_log.events_and_state(
                                name, low_timestamp, high_timestamp
                            )
                        )
                    if trigger.is_active(events):
                        await action.execute(job, self._event_log, high_timestamp)

                self._event_log.subscribe(
                    trigger.topic_names_to_subscribe(), subscriber
                )
        self._create_job_subscriptions_queue.clear()

    def manual_run(self, job_name: str) -> None:
        """
        Execute the Run Action on the specified job.

        Important--when this function returns, it's possible that no events have been
        created yet, not even RUN_REQUESTED.

        TODO consider adding another function manual_run_async that DOES wait until
         the RUN_REQUESTED event has been created
        """
        if job_name not in self._jobs:
            raise ValueError(f"Unknown job: {job_name}")
        job = self._jobs[job_name]
        self._event_loop.call_soon_threadsafe(
            lambda: self._event_loop.create_task(self._run_action(job, Actions.run))
        )

    async def _run_action(self, topic: Topic, action: Action) -> None:
        try:
            await action.execute(topic, self._event_log, self._event_log.curr_timestamp)
        except Exception as e:
            # TODO this function isn't awaited, so exceptions need to make it back into
            #  the scheduler somehow
            print(e)

    def _get_running_and_requested_jobs(
        self, timestamp: Timestamp
    ) -> Iterable[Event[JobPayload]]:
        """
        Returns the latest event for any job that's in RUN_REQUESTED or RUNNING state
        """
        for name in self._jobs.keys():
            ev = self._event_log.last_event(name, timestamp)
            if ev and ev.payload.state in ("RUN_REQUESTED", "RUNNING"):
                yield ev

    def main_loop(self) -> threading.Thread:
        """
        This starts a daemon (background) thread that runs forever. This code just polls
        the job runners, but other code will add callbacks to run on this event loop
        (e.g. EventLog.call_subscribers).
        """

        async def main_loop_helper() -> None:
            while True:
                try:
                    await self._job_runner.poll_jobs(
                        self._get_running_and_requested_jobs(
                            self._event_log.curr_timestamp
                        )
                    )
                except Exception:
                    # TODO do something smarter here...
                    traceback.print_exc()
                await asyncio.sleep(self._job_runner_poll_delay_seconds)

        t = threading.Thread(
            target=lambda: self._event_loop.run_until_complete(main_loop_helper()),
            daemon=True,
        )
        t.start()
        return t

    def all_are_waiting(self) -> bool:
        """
        Returns true if everything is in a "waiting" state. I.e. no jobs are running,
        all subscribers have been processed.
        """
        return self._event_log.all_subscribers_called() and not any(
            True
            for _ in self._get_running_and_requested_jobs(
                self._event_log.curr_timestamp
            )
        )

    def events_of(self, job_name: str) -> List[Event]:
        """For unit tests/debugging"""
        return list(
            self._event_log.events_and_state(
                job_name, 0, self._event_log.curr_timestamp
            )
        )
