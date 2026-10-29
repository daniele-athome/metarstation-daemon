import logging
import shutil

import pytest
# noinspection package-requirements
from pytapo.logger import Logger as PytapoLogger

from metarstation_daemon.backend.interface import WebcamBackendCallback
from metarstation_daemon.backend.tapocam import TapoWebcamBackend


@pytest.fixture
def make_backend():
    backends = []

    def build(debug: bool) -> TapoWebcamBackend:
        backend = TapoWebcamBackend({
            'snapshot_interval_secs': 60,
            'cloud_username': 'admin',
            'cloud_password': 'secret',
            'ip_address': '192.168.1.11',
            'debug': debug,
        }, WebcamBackendCallback())
        backends.append(backend)
        return backend

    yield build
    for backend in backends:
        # noinspection PyProtectedMember
        shutil.rmtree(backend._tempdir, ignore_errors=True)


class TestStreamerLogCallback:
    """The streamer reports a stalled or broken stream through its log function."""

    def test_stream_errors_are_logged_without_debug(self, make_backend, caplog):
        backend = make_backend(debug=False)

        with caplog.at_level(logging.DEBUG):
            backend.streamer_log_callback({
                'currentAction': 'Streaming',
                'streamError': "TimeoutError('no data for 10.0s')",
            })

        assert 'no data for 10.0s' in caplog.text
        assert [r.levelno for r in caplog.records] == [logging.WARNING]

    def test_ffmpeg_logs_only_in_debug(self, make_backend, caplog):
        backend = make_backend(debug=False)

        with caplog.at_level(logging.DEBUG):
            backend.streamer_log_callback({'currentAction': 'Streaming', 'ffmpegLog': 'frame=1'})

        assert not caplog.records

    def test_ffmpeg_logs_in_debug(self, make_backend, caplog):
        backend = make_backend(debug=True)

        with caplog.at_level(logging.DEBUG):
            backend.streamer_log_callback({'currentAction': 'Streaming', 'ffmpegLog': 'frame=1'})

        assert 'frame=1' in caplog.text
        assert [r.levelno for r in caplog.records] == [logging.DEBUG]


def _pytapo_logger(backend: TapoWebcamBackend) -> PytapoLogger:
    """pytapo's own logger, built the way the Tapo constructor builds it from our arguments."""
    # noinspection PyProtectedMember
    args = backend._tapo._tapo_args
    return PytapoLogger(args.get('printDebugInformation', False), args['printWarnInformation'])


class TestPytapoLogging:
    """pytapo prints to stdout unless its logger is handed our logging functions."""

    def test_warnings_reach_the_daemon_log(self, make_backend, caplog, capsys):
        logger = _pytapo_logger(make_backend(debug=False))

        with caplog.at_level(logging.DEBUG):
            logger.warnLog('Temporary Suspension')
            logger.debugLog('chatty')

        assert 'Temporary Suspension' in caplog.text
        assert [r.levelno for r in caplog.records] == [logging.WARNING]
        # debug stays off: pytapo takes any callable as "debug on" and redacts every request
        assert 'chatty' not in caplog.text
        assert capsys.readouterr().out == ''

    def test_debug_reaches_the_daemon_log_in_debug_mode(self, make_backend, caplog, capsys):
        logger = _pytapo_logger(make_backend(debug=True))

        with caplog.at_level(logging.DEBUG):
            logger.debugLog('chatty')

        assert 'chatty' in caplog.text
        assert capsys.readouterr().out == ''
