from enum import Enum

class PolicyType(Enum):
    FCFS = 'fcfs'
    BACKFILL = 'backfill'
    DEADLINE = 'deadline'
    PRIORITY = 'priority'
    SJF = 'sjf'
    

class Policy:

    def __init__(self, strategy):
        self.strategy = PolicyType(strategy)

    def sort_jobs(self, jobs):
        if self.strategy == PolicyType.FCFS or self.strategy == PolicyType.BACKFILL:
            return sorted(jobs, key=lambda job: job.submit_time)
        elif self.strategy == PolicyType.SJF:
            return sorted(jobs, key=lambda job: job.wall_time)
        elif self.strategy == PolicyType.PRIORITY:
            return sorted(jobs, key=lambda job: job.priority, reverse=True)
        else:
            raise ValueError(f"Unknown policy type: {self.policy_type}")

    def find_backfill_job(self, queue, num_free_nodes, current_time):
        """ This implementation is based on pseudocode from Leonenkov and Zhumatiy.
            "Introducing new backfill-based scheduler for slurm resource manager."
            Procedia computer science 66 (2015): 661-669. """

        first_job = queue[0]

        for job in queue: job.end_time = current_time + job.wall_time

        # Sort jobs according to their termination time (end_time)
        sorted_queue = sorted(queue, key=lambda job: job.end_time)

        # Compute shadow time - loop over the list and collect nodes until the 
        # number of available nodes is sufficient for the first job in the queue
        sum_nodes = 0
        shadow_time = None
        for job in sorted_queue:
            sum_nodes += job.nodes_required
            if sum_nodes >= first_job.nodes_required:
                shadow_time = current_time + job.wall_time
                num_extra_nodes = sum_nodes - job.nodes_required
                break

        # Find backfill job
        backfill_job = None
        for job in queue:
            # condition1 checks that the job ends before first_job starts
            condition1 = job.nodes_required <= num_free_nodes \
                         and current_time + job.wall_time < shadow_time
            # condition2 checks that the job does not interfere with first_job
            condition2 = job.nodes_required <= min(num_free_nodes, num_extra_nodes)

            if condition1 or condition2:
                backfill_job = job
                break

        return backfill_job
