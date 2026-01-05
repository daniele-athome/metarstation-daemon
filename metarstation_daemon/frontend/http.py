import logging

import httpx
from httpx import URL

from .interface import DataFrontend
from ..data import SensorData, WebcamData

_LOGGER = logging.getLogger(__name__)


class HTTPDataFrontend(DataFrontend):

    # TODO not a good thing really
    DATA_LIMIT = 50

    def __init__(self, config: dict):
        super().__init__(config)
        self.push_url: str = config['data_url']
        self.image_url: str = config['image_url']
        self.api_token: str = config['api_token']

    async def setup(self):
        # TODO
        _LOGGER.debug("HTTP data frontend starting")

    async def send_data(self, data: list[SensorData]):
        _LOGGER.debug(f"Sending data: {data}")
        async with httpx.AsyncClient() as client:
            auth = BearerTokenAuth(self.api_token)
            r = await client.post(self.push_url, json=[x.to_dict() for x in data[-self.DATA_LIMIT:]], auth=auth)
            if r.status_code not in (200, 201):
                # TODO custom exception maybe?
                raise RuntimeError(f"HTTP request failed with status code {r.status_code}")

    async def send_webcam(self, data: WebcamData):
        _LOGGER.debug(f"Sending webcam snapshot @ {data.timestamp}")
        async with httpx.AsyncClient() as client:
            auth = BearerTokenAuth(self.api_token)
            image_url = URL(self.image_url).copy_add_param('timestamp', data.timestamp.isoformat())
            r = await client.post(image_url, content=data.image_data, auth=auth, headers={
                'content-type': data.image_type,
            })
            if r.status_code not in (200, 201):
                # TODO custom exception maybe?
                raise RuntimeError(f"HTTP request failed with status code {r.status_code}")


class BearerTokenAuth(httpx.Auth):
    def __init__(self, token):
        self.token = token

    def auth_flow(self, request):
        request.headers['Authorization'] = f"Bearer {self.token}"
        yield request
