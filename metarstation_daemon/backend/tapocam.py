import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from pytapo import Tapo
from pytapo.media_stream.streamer import Streamer

from .interface import WebcamBackend, WebcamBackendCallback
from ..data import WebcamData

_LOGGER = logging.getLogger(__name__)
_STREAM_FILENAME = "stream.m3u8"
_IMAGE_TYPE = 'image/jpeg'


async def _print_ffmpeg_logs(stderr):
    _LOGGER.debug('ffmpeg snapshot output:')
    while True:
        line = await stderr.readline()
        if not line:
            break
        _LOGGER.debug(f"  {line.decode().strip()}")


# TODO handle Tapo/Streamer errors!!!
class TapoWebcamBackend(WebcamBackend):

    def __init__(self, config, callback: WebcamBackendCallback):
        super().__init__(config, callback)
        self._snapshot_interval_secs = config['snapshot_interval_secs']
        self._debug = config.get('debug', False)
        # FIXME this constructor BLOCKS (!!) trying to connect to the camera!!!
        #       Also, it throws an exception the connection fails, there is currently no (async) retry mechanism
        self._tapo = Tapo(
            config['ip_address'],
            config['cloud_username'],
            config['cloud_password'],
            config['cloud_password'],
        )
        self._tempdir = tempfile.mkdtemp('weather-station')
        self._streamer = Streamer(
            self._tapo,
            logFunction=self.streamer_log_callback,
            outputDirectory=self._tempdir,
            fileName=_STREAM_FILENAME,
            includeAudio=False,
            mode="hls",
            quality=config.get('quality', 'HD'),
        )
        self._snapshot_last: float = 0
        """Last snapshot timestamp. It will be compared against the stream files to see if the image has actually been produced."""

        self._snapshot_task = None
        self._shutdown_event = asyncio.Event()

    def streamer_log_callback(self, status):
        if self._debug:
            _LOGGER.debug(status)

    async def start(self):
        _LOGGER.debug(f"Tapo webcam starting ({self._streamer.quality} quality)")
        await self._streamer.start()
        self._snapshot_task = asyncio.get_running_loop().create_task(self._collect_snapshot_start())

    async def stop(self):
        _LOGGER.debug("Tapo webcam stopping")
        await self._streamer.stop()

        self._shutdown_event.set()
        if self._snapshot_task:
            self._snapshot_task.cancel()

        # cleanup temporary files
        shutil.rmtree(self._tempdir, ignore_errors=True)

    async def _collect_snapshot_start(self):
        _LOGGER.debug("Starting webcam snapshot collection")

        while not self._shutdown_event.is_set():
            try:
                # wait for the shutdown event or the collect interval, whichever comes first
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._snapshot_interval_secs
                )
            except asyncio.TimeoutError:
                pass

            if self._shutdown_event.is_set():
                break

            _LOGGER.debug("Taking snapshot from webcam")
            await self._take_snapshot()

    async def _take_snapshot(self):
        """
        ffmpeg -i stream_output.m3u8 -vframes 1 -q:v 10 snapshot.jpg, but with pipes.
        """

        if not self._stream_changed():
            _LOGGER.debug('Stream did not change, not taking snapshot')
            return

        cmd = [
            'ffmpeg',
            '-i',
            str(self._stream_file()),
            '-an',
            '-c:v',
            'mjpeg',
            '-pix_fmt',
            'yuvj420p',
            '-vframes',
            '1',
            '-q:v',
            '10',
            '-f',
            'image2pipe',
            'pipe:1',
        ]
        _LOGGER.debug(f"cmdline: {cmd}")
        self._snapshot_process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        snapshot_data = await self._snapshot_process.stdout.read()
        await self._snapshot_process.wait()
        if self._snapshot_process.returncode == 0:
            # TEST write to file
            if self._debug:
                with open(Path(self._tempdir) / "snapshot.jpg", "wb") as f:
                    f.write(snapshot_data)
            self.callback.update(WebcamData(image_data=snapshot_data, image_type=_IMAGE_TYPE))
        else:
            await _print_ffmpeg_logs(self._snapshot_process.stderr)

    def _stream_changed(self) -> bool:
        stream_stat = self._stream_file().stat()
        if self._snapshot_last != stream_stat.st_mtime:
            self._snapshot_last = stream_stat.st_mtime
            return True
        else:
            return False

    def _stream_file(self):
        return Path(self._tempdir) / _STREAM_FILENAME
