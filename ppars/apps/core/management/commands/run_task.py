from django.core.management.base import BaseCommand
from ppars.apps.core.tasks import prerefill_job, midnight_job
from ppars.apps.notification.tasks import news_message_job


class Command(BaseCommand):
    '''
    run_task
    '''
    args = 'no args'
    help = 'send_emails_for_tags_followers'

    def handle(self, *args, **options):
        print "start core"
        # prerefill_job()
        midnight_job()
        # news_message_job.delay()
        print "done"