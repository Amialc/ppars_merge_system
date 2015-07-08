import tempfile
import csv
import urllib
import json
import logging
import requests
from django.conf import settings
from django.template.defaultfilters import slugify


logger = logging.getLogger('ppars')


def debug(*args, **kwargs):
    logger.debug('%s, %s' % (args, kwargs))

'''
def import_pins(file):
    temp_file = tempfile.TemporaryFile()
    output = []
    for chunk in file.chunks():
            temp_file.write(chunk)
    temp_file.seek(0)
    format = os.path.splitext(file.name)[1].lower()
    if format in ['.csv', '.tsv']:
	reader = csv.reader(temp_file)
	reader.next()
	for row in reader:
		output.append(row[0])
    if format in ['.xls', '.xlsx']:

	workbook = xlrd.open_workbook(file_contents=temp_file.read())
	worksheet = workbook.sheet_by_name(workbook.sheet_names()[0])
	num_rows = worksheet.nrows - 1
	curr_row = 0
	while curr_row < num_rows:
		curr_row += 1
		row = worksheet.row(curr_row)
		output.append(row[0].value)
    temp_file.close
    return output
'''


def import_csv(file):
    temp_file = tempfile.TemporaryFile()
    output = []
    #for chunk in file.chunks():
            #temp_file.write(chunk)
    # temp_file.seek(0)
    reader = csv.reader(file.read().splitlines())
    cols = reader.next()
    for row in reader:
        entry = dict()
        for i in range(len(cols)):
                entry[slugify(cols[i]).replace('-','_')] = row[i] or None
        output.append(entry)
    temp_file.close
    return output


def url_with_querystring(path, **kwargs):
    return path + '?' + urllib.urlencode(kwargs)


def mandrill_emailsend(key,emailBody, emailSubject, efrom, to):
    if not settings.TEST_MODE:
        form_fields = {
            "key": key,
            "message": {
                "html": emailBody,
                "subject": emailSubject,
                "from_email": efrom,
                "to": [{
                    "email": to,
                    "type": "to",
                }],
            }
        }
        # result = urlfetch.fetch(url='https://mandrillapp.com/api/1.0/messages/send.json',
            # payload=json.dumps(form_fields),
        #     method=urlfetch.POST,
        #     headers={'Content-Type': 'application/json'}
        # )
        result = requests.post('https://mandrillapp.com/api/1.0/messages/send.json',
                               data=json.dumps(form_fields),
                               headers={'Content-Type': 'application/json'})
        return result
    return False
