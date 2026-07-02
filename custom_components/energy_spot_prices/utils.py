import re
from collections import defaultdict
from datetime import timedelta


def get_interval_minutes(iso8601_interval: str) -> int:
    """
    Convert an ISO 8601 duration string to total minutes.
    Example: 'PT15M' -> 15, 'PT1H' -> 60
    """
    # Handle hour format (PT1H, PT2H, etc.)
    hour_match = re.match(r"PT(\d+)H", iso8601_interval)
    if hour_match:
        return int(hour_match.group(1)) * 60
    
    # Handle minute format (PT15M, PT60M, etc.)
    minute_match = re.match(r"PT(\d+)M", iso8601_interval)
    if minute_match:
        return int(minute_match.group(1))
    
    raise ValueError(f"Unsupported ISO 8601 interval format: {iso8601_interval}")


def bucket_time(ts, bucket_size):
    """
    Get the bucket time for the interval.

    e.g. for a bucket size of 15 minutes, the time 10:07 would be rounded down to 10:00,
    """
    return ts - timedelta(
        minutes=ts.minute % bucket_size, seconds=ts.second, microseconds=ts.microsecond
    )


def average_to_interval(data: dict, expected_interval: int) -> dict:
    """
    Average prices into the expected interval buckets

    args:
        data: The data to average
        expected_interval: The interval in minutes after transformation (e.g. 30, 60)
    """

    # Create buckets of expected_interval
    by_hour = defaultdict(list)
    for timestamp, price in data.items():
        bucket = bucket_time(timestamp, expected_interval)
        by_hour[bucket].append(price)

    # Calculate the average for each bucket
    return {
        hour: round(sum(prices) / len(prices), 2)
        for hour, prices in by_hour.items()
    }
