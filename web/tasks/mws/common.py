import hashlib
import json
import redis
import requests
import os
import time
import amazonmws as amz_mws

from app import celery_app
from celery.five import monotonic


########################################################################################################################


mws_priority_limits = {
    'GetServiceStatus': {
        0: {
            'quota_max': 1
        },

        1: {
            'quota_max': 1
        },

        2: {
            'quota_max': 2
        }
    },

    'ListMatchingProducts': {
        0: {
            'quota_max': 10,
        },

        1: {
            'quota_max': 15,
        },

        2: {
            'quota_max': 20
        }
    },

    'GetMyFeesEstimate': {
        0: {
            'quota_max': 10,
        },

        1: {
            'quota_max': 15,
        },

        2: {
            'quota_max': 20
        }
    },

    'GetCompetitivePricingForASIN': {
        0: {
            'quota_max': 10
        },

        1: {
            'quota_max': 15
        },

        2: {
            'quota_max': 20
        }
    },

    'ListInventorySupply': {
        0: {
            'quota_max': 20
        },

        1: {
            'quota_max': 25
        },

        2: {
            'quota_max': 30
        }
    }
}


########################################################################################################################


class MWSTask(celery_app.Task):
    """Common behaviors for all MWS API calls."""
    cache_ttl = None
    soft_time_limit = 30
    pending_expires = 200
    restore_rate_adjust = 0
    wait_adjust = 0

    request_timeout = (10, 30)  # The connect and read timeouts for requests.get()

    options = {
        'default_retry_delay': 10,
        'retry_backoff': 5,
        'max_retries': 6,
        'autoretry_for': (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout
        )
    }

    def _use_requests(self, method, **kwargs):
        """Adapter function that lets the amazonmws library use requests."""
        if method == 'POST':
            return requests.post(**kwargs)
        elif method == 'GET':
            response = requests.get(**kwargs, timeout=self.request_timeout)
            if response.status_code == 500:
                self.retry()
        else:
            raise ValueError('Unsupported HTTP method: ' + method)

    def __init__(self):
        """Initialize the task object."""
        # Set up database connections here
        self.api = None
        self.redis = redis.from_url(os.environ.get('CELERY_REDIS_URL', 'redis://'))
        self._credentials = {
            'access_key': os.environ.get('MWS_ACCESS_KEY', 'test_access_key'),
            'secret_key': os.environ.get('MWS_SECRET_KEY', 'test_secret_key'),
            'seller_id': os.environ.get('MWS_SELLER_ID', 'test_account_id')
        }
        self._pa_credentials = {
          "access_key": os.environ.get('PA_ACCESS_KEY', 'test_access_key'),
          "secret_key": os.environ.get('PA_SECRET_KEY', 'test_secret_key'),
          "account_id": os.environ.get('PA_ASSOCIATE_TAG', 'test_associate_tag')
        }

        self._cache_key = None
        self._cached_value = None
        self._action_name = None
        self._api_name = None
        self._limits = {}
        self._usage = {}

    def __call__(self, *args, **kwargs):
        """Perform the API call."""
        self._api_name, self._action_name = self.name.split('.')[-2:]
        if self._api_name == 'product_adv':
            self._api_name = 'ProductAdvertising'
        elif self._api_name == 'inventory':
            self._api_name = 'FulfillmentInventory'
        else:
            self._api_name = self._api_name.capitalize()

        # Check the cache
        if kwargs.pop('use_cache', True):
            self._cache_key = self.build_cache_key(*args, **kwargs)
            self._cached_value = self.get_cached_value()

        if self._cached_value is not None:
            self.run = self.return_cached_value
        else:
            self.run = self.make_api_call

        return super().__call__(*args, **kwargs)

    def return_cached_value(self, *args, **kwargs):
        return self._cached_value

    def load_usage(self):
        """Load usage for this operation type, and increment the pending counter for this operation."""
        usage_key = self.name + '_usage'

        now = monotonic()
        restore_rate = self._limits['restore_rate']

        # The script below restores the quota level based on the elapsed time and the restore rate
        # Call like so: EVAL script 1 usage_key current_time restore_rate
        script = f"""
        local usage = redis.call('HMGET', '{usage_key}', 'quota_level', 'pending', 'last_request')
        local quota_level = true and tonumber(usage[1]) or 0
        local pending = true and tonumber(usage[2]) or 0
        local last_request = true and tonumber(usage[3]) or 0
        local restored = 0
        
        if last_request > 0 then
            restored = ({now} - last_request) / {restore_rate}
            quota_level = math.max(quota_level - restored, 0)
            redis.call('HSET', '{usage_key}', 'quota_level', quota_level)
        end
        
        redis.call('HINCRBY', '{usage_key}', 'pending', 1)
        redis.call('EXPIRE', '{usage_key}', {self.pending_expires})
        
        return {{tostring(quota_level), pending, tostring(last_request), tostring(restored)}}
        """

        values = self.redis.eval(script, 0)

        usage = {
            'quota_level': float(values[0]),
            'pending': int(values[1]),
            'last_request': float(values[2])
        }

        self._usage = usage
        print(f"{usage_key}: "
              f"quota_level={usage['quota_level']} "
              f"pending={usage['pending']} "
              f"last_request={usage['last_request']} "
              f"now={now} "
              f"restored={values[-1]}")

    def calculate_wait(self):
        """Calculate how long to sleep() before making the API call."""
        quota_max = self._limits['quota_max']
        restore_rate = self._limits['restore_rate']
        quota_level = self._usage['quota_level']
        pending = self._usage['pending']

        return max((quota_level + pending + 1 - quota_max), 0) * restore_rate

    def make_api_call(self, *args, **kwargs):
        """Make the api call, save the value to the cache, and update usage statistics."""
        priority = self.get_priority()
        kwargs.pop('priority', None)

        self.load_api()
        self.load_throttle_limits(priority)
        self.load_usage()

        try:
            wait = self.calculate_wait()
            time.sleep(wait + self.wait_adjust)
            return_value = getattr(self.api, self._action_name)(*args, **kwargs).text
        except Exception as e:
            self.save_usage()
            raise e

        self.save_usage()
        self.save_to_cache(return_value)
        return return_value

    def save_usage(self):
        """Update the usage stats in the cache."""
        usage_key = self.name + '_usage'
        now = monotonic()
        restore_rate = self._limits['restore_rate']

        # The script below restores the quota level based on the elapsed time and the restore rate
        # Call like so: EVAL script 1 usage_key current_time restore_rate
        script = f"""
                local usage = redis.call('HMGET', '{usage_key}', 'quota_level', 'last_request')
                local quota_level = true and tonumber(usage[1]) or 0
                local last_request = true and tonumber(usage[2]) or 0
                local restored = 0
                
                if last_request > 0 then
                    local restored = ({now} - last_request) / {restore_rate}
                    quota_level = math.max(quota_level - restored, 0)
                    redis.call('HSET', '{usage_key}', 'quota_level', quota_level + 1)
                end
                
                redis.call('HINCRBY', '{usage_key}', 'pending', -1)
                redis.call('HSET', '{usage_key}', 'last_request', {now})
                
                return {{tostring(restored), tostring(last_request)}}
                """

        values = self.redis.eval(script, 0)

    def load_throttle_limits(self, priority):
        """Load custom throttle limits based on a task's name and priority."""
        try:
            priority_ceil = max(mws_priority_limits[self._action_name])
        except KeyError:
            priority_ceil = 0

        try:
            priority = min(int(priority), priority_ceil)
        except TypeError:
            print(f'Invalid priority value: {priority}\nUsing default priority (0)')
            priority = 0

        self._limits = {
            'quota_max': 1,
            'restore_rate': 0,
        }

        self._limits.update(
            **amz_mws.DEFAULT_LIMITS.get(self._action_name, {})
        )

        self._limits.update(
            **mws_priority_limits.get(self._action_name, {}).get(priority, {})
        )

    def load_api(self):
        """Loads the correct API object from amz_mws, based on the module name of the current task."""
        self.api = getattr(amz_mws, self._api_name)(
            **self._pa_credentials if self._api_name == 'ProductAdvertising' else self._credentials,
            make_request=self._use_requests
        )

    def build_cache_key(self, *args, **kwargs):
        """Build a key to store/retrieve cache values in redis. The key signature is build from the class name and
        the call signature."""
        if not self.cache_ttl:
            return None
        else:
            kwargs.pop('priority', None)

            sig = hashlib.md5(
                json.dumps({'args': args, 'kwargs': kwargs}).encode()
            ).hexdigest()

            return f'{self.name}_{sig}'

    def get_cached_value(self):
        """Return the value in the cache corresponding to the given args and kwargs, or None."""
        if not self.cache_ttl or not self.redis.exists(self._cache_key):
            return None
        else:
            return self.redis.get(self._cache_key).decode()

    def save_to_cache(self, value):
        """Save the value to the cache."""
        if self.cache_ttl:
            self.redis.set(self._cache_key, value, ex=self.cache_ttl)

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        """Clean up."""
        self.api = None
        self._cache_key = None
        self._cached_value = None
        self._action_name = None
        self._api_name = None
        self._limits = {}
        self._usage = {}


