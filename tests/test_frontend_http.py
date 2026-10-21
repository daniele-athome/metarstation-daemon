import httpx
import pytest

from metarstation_daemon.frontend.http import HTTPDataFrontend
from metarstation_daemon.frontend.interface import DataFrontendError, DataRejectedError


@pytest.fixture
async def frontend():
    """An HTTP frontend answering every request with the status code in `frontend.status_code`."""
    frontend = HTTPDataFrontend({
        'data_url': 'http://localhost/push',
        'image_url': 'http://localhost/image',
        'api_token': 'token',
    })
    frontend.status_code = 200
    # noinspection PyProtectedMember
    frontend._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(frontend.status_code)))
    yield frontend
    # noinspection PyProtectedMember
    await frontend._client.aclose()


@pytest.fixture(params=['data', 'webcam'])
def send(request, frontend, sensor_data_full, webcam_data):
    """Both send methods must classify failures the same way."""
    if request.param == 'data':
        return lambda: frontend.send_data([sensor_data_full])
    return lambda: frontend.send_webcam(webcam_data)


@pytest.mark.parametrize('status_code', [200, 201, 202, 204])
async def test_success(frontend, send, status_code):
    frontend.status_code = status_code

    await send()


@pytest.mark.parametrize('status_code', [400, 404, 413, 422])
async def test_data_rejected(frontend, send, status_code):
    frontend.status_code = status_code

    with pytest.raises(DataRejectedError):
        await send()


@pytest.mark.parametrize('status_code', [
    # not the data's fault
    401, 403, 408, 429,
    # server errors
    500, 502, 503,
    # redirects are not followed
    301,
])
async def test_temporary_failure(frontend, send, status_code):
    frontend.status_code = status_code

    with pytest.raises(DataFrontendError) as e:
        await send()

    # the data can be sent again
    assert not isinstance(e.value, DataRejectedError)
