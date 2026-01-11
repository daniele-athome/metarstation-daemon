import asyncio
import datetime
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from kasa import Discover as kasa_Discover, Credentials as kasa_Credentials

from pytapo import Tapo
from pytapo.media_stream.streamer import Streamer

from .interface import WebcamBackend, WebcamBackendCallback
from ..data import WebcamData

_LOGGER = logging.getLogger(__name__)
_STREAM_FILENAME = "stream.m3u8"
_IMAGE_TYPE = 'image/jpeg'
_STREAM_SETTLE_WAIT_SECS = 10


async def _print_ffmpeg_logs(stderr):
    _LOGGER.debug('ffmpeg snapshot output:')
    while True:
        line = await stderr.readline()
        if not line:
            break
        _LOGGER.debug(f"  {line.decode().strip()}")


class TapoStreamer:

    def __init__(self, quality: str,
                 log_callback, connect_callback,
                 tempdir: str,
                 discovery_interface: str,
                 discovery_username: str,
                 discovery_password: str,
                 tapo_args: dict):
        self.quality = quality
        self._log_callback = log_callback
        self._connect_callback = connect_callback
        self._tempdir = tempdir
        self._discovery_interface = discovery_interface
        self._discovery_username = discovery_username
        self._discovery_password = discovery_password
        self._tapo_args = tapo_args
        self._connect_task: asyncio.Task | None = None
        self._tapo: Tapo | None = None
        self._streamer: Streamer | None = None
        self._shutdown_event = asyncio.Event()
        self.ready = False

    async def start(self):
        if 'host' in self._tapo_args and self._tapo_args['host'] is not None:
            self._connect_task = asyncio.get_running_loop().create_task(self._connect())
        elif self._discovery_interface is not None:
            self._connect_task = asyncio.get_running_loop().create_task(self._discover())
        else:
            raise ValueError('Provide either camera host or discovery interface')

    async def stop(self):
        self._shutdown_event.set()
        if self._connect_task:
            self._connect_task.cancel()
        await self.pause_stream()
        self.ready = False

    async def resume_stream(self):
        _LOGGER.debug('Resuming stream')
        # this is safe to call, no blocking stuff
        self._streamer = Streamer(
            self._tapo,
            logFunction=self._log_callback,
            outputDirectory=self._tempdir,
            fileName=_STREAM_FILENAME,
            includeAudio=False,
            mode="hls",
            quality=self.quality,
        )
        await self._streamer.start()

    async def pause_stream(self):
        if self._streamer:
            # _LOGGER.debug(f'Pausing stream - status: {self._streamer.currentAction}')
            try:
                if self._streamer.streamProcess:
                    self._streamer.streamProcess.kill()
                    # remove all contents of tempdir
                    [f.unlink() for f in Path(self._tempdir).glob("*") if f.is_file()]
                await self._streamer.stop()
                self._streamer = None
            except:
                pass

    async def _discover(self):
        while not self._shutdown_event.set():
            _LOGGER.debug(f'Discovering camera on interface {self._discovery_interface}')
            if self._discovery_username and self._discovery_password:
                credentials = kasa_Credentials(
                    username=self._discovery_username,
                    password=self._discovery_password,
                )
            else:
                credentials = None

            try:
                devices = await kasa_Discover.discover(
                    interface=self._discovery_interface,
                    credentials=credentials,
                    discovery_timeout=5,
                )
                if len(devices) > 0:
                    # provide the host to the Tapo constructor and continue with normal connection
                    self._tapo_args['host'] = next(iter(devices.keys()))
                    await self.start()
                    break

            except asyncio.CancelledError:
                break
            except:
                _LOGGER.warning('Error discovering camera', exc_info=True)
                try:
                    # TODO exponential backoff?
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    break

    async def _connect(self):
        _LOGGER.debug(f'Connecting to camera at address {self._tapo_args["host"]}')
        while not self._shutdown_event.set():
            try:
                self._tapo = await asyncio.get_running_loop().run_in_executor(None, self._create_tapo)
                self.ready = True

                if self._connect_callback:
                    self._connect_callback()
                break

            except OSError as e:
                # network error, queue a retry after some time
                _LOGGER.warning(f'Tapo connect failed! {e}')
                try:
                    # TODO exponential backoff?
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    break

    def _create_tapo(self):
        """Called from an executor because the Tapo constructor will block for connecting to the camera."""
        return Tapo(**self._tapo_args)


class TapoWebcamBackend(WebcamBackend):

    def __init__(self, config, callback: WebcamBackendCallback):
        super().__init__(config, callback)
        self._snapshot_interval_secs = max(config['snapshot_interval_secs'], _STREAM_SETTLE_WAIT_SECS * 2)
        self._debug = config.get('debug', False)
        self._tempdir = tempfile.mkdtemp('weather-station')
        self._tapo = TapoStreamer(
            quality=config.get('quality', 'HD'),
            log_callback=self.streamer_log_callback,
            connect_callback=self.streamer_connected,
            tempdir=self._tempdir,
            discovery_interface=config.get('discovery_interface', None),
            discovery_username=config.get('discovery_username', None),
            discovery_password=config.get('discovery_password', None),
            tapo_args={
                'host': config.get('ip_address', None),
                'user': config['cloud_username'],
                'password': config['cloud_password'],
                'cloudPassword': config['cloud_password'],
            },
        )
        self._snapshot_last: float = 0
        """Last snapshot timestamp. It will be compared against the stream files to see if the image has actually been produced."""

        self._snapshot_task = None
        self._shutdown_event = asyncio.Event()

    def streamer_log_callback(self, status):
        if self._debug:
            _LOGGER.debug(status)

    def streamer_connected(self):
        _LOGGER.info('Tapo webcam connected')
        self._snapshot_task = asyncio.get_running_loop().create_task(self._collect_snapshot_start())

    async def start(self):
        _LOGGER.info(f"Tapo webcam starting ({self._tapo.quality} quality)")
        await self._tapo.start()

    async def stop(self):
        _LOGGER.info("Tapo webcam stopping")
        await self._tapo.stop()

        self._shutdown_event.set()
        if self._snapshot_task:
            self._snapshot_task.cancel()

        # cleanup temporary files
        shutil.rmtree(self._tempdir, ignore_errors=True)

    async def _collect_snapshot_start(self):
        _LOGGER.debug("Starting webcam snapshot collection")

        while not self._shutdown_event.is_set():
            try:
                if not self._tapo.ready:
                    _LOGGER.debug('Connection to camera not ready, not taking snapshot')
                    continue

                await self._tapo.resume_stream()
                try:
                    # we currently don't have a way with pytapo API to check for readiness,
                    # so we just sleep, hoping the stream will be ready by then
                    await asyncio.sleep(_STREAM_SETTLE_WAIT_SECS)
                except asyncio.CancelledError:
                    break

                await self._take_snapshot()
            except asyncio.CancelledError:
                break
            except:
                _LOGGER.warning("Error taking snapshot from webcam", exc_info=True)
            finally:
                await self._tapo.pause_stream()

            await asyncio.sleep(self._snapshot_interval_secs - _STREAM_SETTLE_WAIT_SECS)

    async def _take_snapshot(self):
        """
        ffmpeg -i stream_output.m3u8 -vframes 1 -q:v 10 snapshot.jpg, but with pipes.
        """

        if not self._stream_changed():
            _LOGGER.debug('Stream did not change, not taking snapshot')
            return

        _LOGGER.debug("Taking snapshot from webcam")

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
            self.callback.update(WebcamData(
                timestamp=datetime.datetime.now(datetime.UTC),
                image_data=snapshot_data,
                image_type=_IMAGE_TYPE,
            ))
        else:
            await _print_ffmpeg_logs(self._snapshot_process.stderr)

    def _stream_changed(self) -> bool:
        stream_file = self._stream_file()
        if not stream_file.exists():
            return False

        stream_stat = stream_file.stat()
        if self._snapshot_last != stream_stat.st_mtime:
            self._snapshot_last = stream_stat.st_mtime
            return True
        else:
            return False

    def _stream_file(self):
        return Path(self._tempdir) / _STREAM_FILENAME
