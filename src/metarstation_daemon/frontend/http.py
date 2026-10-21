import logging

import httpx
from httpx import URL

from .interface import DataFrontend, DataFrontendError, DataRejectedError
from ..data import SensorData, WebcamData

_LOGGER = logging.getLogger(__name__)


def _check_response(response: httpx.Response):
    if response.is_success:
        return
    if response.is_client_error and response.status_code not in (401, 403, 408, 429):
        raise DataRejectedError(f"HTTP request rejected with status code {response.status_code}")
    # some errors can be retried
    raise DataFrontendError(f"HTTP request failed with status code {response.status_code}")


class HTTPDataFrontend(DataFrontend):
    # TODO not a good thing really
    DATA_LIMIT = 50

    def __init__(self, config: dict):
        super().__init__(config)
        self.push_url: str = config['data_url']
        self.image_url: str = config['image_url']
        self.api_token: str = config['api_token']
        self.timeout_secs: int = config.get('timeout_secs', 10)
        self._client: httpx.AsyncClient

    async def setup(self):
        _LOGGER.debug("HTTP data frontend starting")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=self.timeout_secs),
            auth=BearerTokenAuth(self.api_token),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    async def send_data(self, data: list[SensorData]):
        _LOGGER.debug(f"Sending data: {data}")
        r = await self._client.post(self.push_url, json=[x.to_dict() for x in data[-self.DATA_LIMIT:]])
        _check_response(r)

    async def send_webcam(self, data: WebcamData):
        _LOGGER.debug(f"Sending webcam snapshot @ {data.expire}")
        image_url = URL(self.image_url).copy_add_param('expire', data.expire.isoformat())
        r = await self._client.post(image_url, content=data.image_data, headers={
            'content-type': data.image_type,
        })
        _check_response(r)


class BearerTokenAuth(httpx.Auth):
    def __init__(self, token):
        self.token = token

    def auth_flow(self, request):
        request.headers['Authorization'] = f"Bearer {self.token}"
        yield request
