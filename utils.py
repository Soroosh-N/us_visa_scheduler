from datetime import datetime, timedelta


def get_tomorrow():
    tomorrow = datetime.now() + timedelta(days=1)
    return tomorrow.strftime('%Y-%m-%d')
