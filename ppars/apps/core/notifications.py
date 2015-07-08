__author__ = 'eugene'

messages = {
    'dollar_phone_errors': {
        'enter_digit_token': 'Please login to the Dollarphone and enter digit token.'
    },
    'queue_precharge': {
        'company_failed': {     # at least one word must be in error message
            'pin': ['pin', 'rpm'],
            'failed': ['failed', 'failure']
        },
        'customer_failed': {   # 3 type of errors. All word must be in error message
            'not_charge': ['declined', 'charge'],
            'invalid_cvc': ['invalid', 'cvc'],
            'billing_adr': ['billing', 'address']
        }
    }
}
