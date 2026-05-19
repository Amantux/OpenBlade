"""Simple synchronous worker."""

from collections.abc import Callable
from typing import TypeVar

from openblade.domain.models import Job
from openblade.jobs.queue import JobQueue

ResultT = TypeVar("ResultT")


class Worker:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    def run(self, job: Job, func: Callable[[], ResultT]) -> tuple[Job, ResultT]:
        return self.queue.run_job(job, func)
