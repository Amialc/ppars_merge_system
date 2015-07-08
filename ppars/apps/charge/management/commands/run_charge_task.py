from django.core.management.base import BaseCommand
from ppars.apps.charge.tasks import precharge_job


class Command(BaseCommand):
    '''
    run_charge_task
    '''

    def handle(self, *args, **options):
        print "start run_charge_task"
        # precharge_job.delay()
        print "done"