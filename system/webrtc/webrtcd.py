#!/usr/bin/env python3
import argparse
import asyncio
import json
import uuid
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

import capnp
from aiohttp import web

if TYPE_CHECKING:
  from aiortc.rtcdatachannel import RTCDataChannel

try:
  from openpilot.system.webrtc.schema import generate_field
except ImportError:
  try:
    from selfdrive.ui.webrtc.schema import generate_field
  except ImportError:
    def generate_field(*args):
      return {}

from cereal import messaging, log

# 로깅 설정 (디버깅용)
logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')


class CerealOutgoingMessageProxy:
  def __init__(self, sm: messaging.SubMaster):
    self.sm = sm
    self.channels: list[RTCDataChannel] = []

  def add_channel(self, channel: 'RTCDataChannel'):
    self.channels.append(channel)

  def to_json(self, msg_content: Any):
    if isinstance(msg_content, capnp._DynamicStructReader):
      msg_dict = msg_content.to_dict()
    elif isinstance(msg_content, capnp._DynamicListReader):
      msg_dict = [self.to_json(msg) for msg in msg_content]
    elif isinstance(msg_content, bytes):
      msg_dict = msg_content.decode()
    else:
      msg_dict = msg_content
    return msg_dict

  async def update(self):
    self.sm.update(0)
    for service, updated in self.sm.updated.items():
      if not updated:
        continue
      msg_dict = self.to_json(self.sm[service])
      mono_time, valid = self.sm.logMonoTime[service], self.sm.valid[service]
      outgoing_msg = {"type": service, "logMonoTime": mono_time, "valid": valid, "data": msg_dict}
      encoded_msg = json.dumps(outgoing_msg).encode()
      for channel in self.channels:
        if isinstance(channel, web.WebSocketResponse):
          await channel.send_bytes(encoded_msg)
        else:
          channel.send(encoded_msg)


class CerealIncomingMessageProxy:
  def __init__(self, pm: messaging.PubMaster):
    self.pm = pm

  def send(self, message: bytes):
    msg_json = json.loads(message)
    msg_type, msg_data = msg_json["type"], msg_json["data"]
    size = None
    if not isinstance(msg_data, dict):
      size = len(msg_data)
    msg = messaging.new_message(msg_type, size=size)
    setattr(msg, msg_type, msg_data)
    self.pm.send(msg_type, msg)


class CerealProxyRunner:
  def __init__(self, proxy: CerealOutgoingMessageProxy):
    self.proxy = proxy
    self.task = None
    self.logger = logging.getLogger("webrtcd")

  def start(self):
    if self.task is None:
      self.task = asyncio.create_task(self.run())

  def stop(self):
    if self.task:
      self.task.cancel()
      self.task = None

  async def run(self):
    from aiortc.exceptions import InvalidStateError
    while True:
      try:
        await self.proxy.update()
      except InvalidStateError:
        break
      except asyncio.CancelledError:
        break
      except Exception:
        self.logger.exception("Cereal outgoing proxy failure")
      await asyncio.sleep(0.01)


class DynamicPubMaster(messaging.PubMaster):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.lock = asyncio.Lock()

  async def add_services_if_needed(self, services):
    async with self.lock:
      for service in services:
        if service not in self.sock:
          self.sock[service] = messaging.pub_sock(service)


class StreamSession:
  shared_pub_master = DynamicPubMaster([])

  def __init__(self, sdp: str, cameras: list[str], incoming_services: list[str], outgoing_services: list[str],
               debug_mode: bool = False):
    from aiortc.mediastreams import VideoStreamTrack, AudioStreamTrack
    from aiortc.contrib.media import MediaBlackhole
    from openpilot.system.webrtc.device.video import LiveStreamVideoStreamTrack
    from openpilot.system.webrtc.device.audio import AudioInputStreamTrack, AudioOutputSpeaker
    from teleoprtc import WebRTCAnswerBuilder
    from teleoprtc.info import parse_info_from_offer

    self.logger = logging.getLogger("webrtcd")
    config = parse_info_from_offer(sdp)
    builder = WebRTCAnswerBuilder(sdp)

    for cam in cameras:
      try:
        track = LiveStreamVideoStreamTrack(cam) if not debug_mode else VideoStreamTrack()
        builder.add_video_stream(cam, track)
        self.logger.info(f"added camera track: {cam}")
      except Exception:
        self.logger.exception(f"failed to create camera track: {cam}")
        raise

    if config.expected_audio_track:
      builder.add_audio_stream(AudioInputStreamTrack() if not debug_mode else AudioStreamTrack())
    if config.incoming_audio_track:
      self.audio_output_cls = AudioOutputSpeaker if not debug_mode else MediaBlackhole
      builder.offer_to_receive_audio_stream()

    self.stream = builder.stream()
    self.identifier = str(uuid.uuid4())
    self.incoming_bridge = None
    self.outgoing_bridge_runner = None

    if len(incoming_services) > 0:
      self.incoming_bridge = CerealIncomingMessageProxy(self.shared_pub_master)
    if len(outgoing_services) > 0:
      self.outgoing_bridge = CerealOutgoingMessageProxy(messaging.SubMaster(outgoing_services))
      self.outgoing_bridge_runner = CerealProxyRunner(self.outgoing_bridge)

    self.audio_output = None
    self.run_task = None
    self.logger.info(f"New stream session ({self.identifier})")

  def start(self):
    self.run_task = asyncio.create_task(self.run())

  def stop(self):
    # FIX: Don't use asyncio.run() here to avoid crash
    if self.run_task:
      self.run_task.cancel()
      self.run_task = None

    # Schedule cleanup on the loop
    loop = asyncio.get_event_loop()
    if loop.is_running():
      loop.create_task(self.post_run_cleanup())

  async def get_answer(self):
    return await self.stream.start()

  async def message_handler(self, message: bytes):
    if self.incoming_bridge:
      try:
        self.incoming_bridge.send(message)
      except Exception:
        self.logger.exception("Incoming bridge error")

  async def run(self):
    try:
      await self.stream.wait_for_connection()
      if self.stream.has_messaging_channel():
        if self.incoming_bridge:
          await self.shared_pub_master.add_services_if_needed(self.incoming_bridge.services)  # fix access
          self.stream.set_message_handler(self.message_handler)
        if self.outgoing_bridge_runner:
          channel = self.stream.get_messaging_channel()
          self.outgoing_bridge_runner.proxy.add_channel(channel)
          self.outgoing_bridge_runner.start()

      if self.stream.has_incoming_audio_track():
        track = self.stream.get_incoming_audio_track(buffered=False)
        self.audio_output = self.audio_output_cls()
        self.audio_output.addTrack(track)
        self.audio_output.start()

      self.logger.info(f"Stream session ({self.identifier}) connected")
      await self.stream.wait_for_disconnection()
    except asyncio.CancelledError:
      pass
    except Exception:
      self.logger.exception("Stream session failure")
    finally:
      await self.post_run_cleanup()

  async def post_run_cleanup(self):
    try:
      await self.stream.stop()
      if self.outgoing_bridge_runner:
        self.outgoing_bridge_runner.stop()
      if self.audio_output:
        self.audio_output.stop()
    except Exception:
      pass


@dataclass
class StreamRequestBody:
  sdp: str
  cameras: list[str]
  bridge_services_in: list[str] = field(default_factory=list)
  bridge_services_out: list[str] = field(default_factory=list)


async def get_stream(request: 'web.Request'):
  stream_dict, debug_mode = request.app['streams'], request.app['debug']
  try:
    raw_body = await request.json()
  except Exception:
    return web.json_response({"error": "Invalid JSON"}, status=400)

  body = StreamRequestBody(**raw_body)
  session = StreamSession(body.sdp, body.cameras, body.bridge_services_in, body.bridge_services_out, debug_mode)
  answer = await session.get_answer()
  session.start()
  stream_dict[session.identifier] = session

  return web.json_response({"sdp": answer.sdp, "type": answer.type})


async def get_schema(request: 'web.Request'):
  try:
    services = request.query["services"].split(",")
    services = [s for s in services if s]
    schema_dict = {s: generate_field(log.Event.schema.fields[s]) for s in services}
    return web.json_response(schema_dict)
  except Exception as e:
    return web.json_response({"error": str(e)}, status=400)


async def on_shutdown(app: 'web.Application'):
  for session in app['streams'].values():
    session.stop()
  del app['streams']


# --- Robust CORS Middleware ---
@web.middleware
async def cors_middleware(request, handler):
  if request.method == 'OPTIONS':
    return web.Response(status=200, headers={
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      'Access-Control-Max-Age': '86400',
    })
  try:
    response = await handler(request)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response
  except Exception as e:
    return web.json_response({"error": str(e)}, status=500, headers={'Access-Control-Allow-Origin': '*'})


def webrtcd_thread(host: str, port: int, debug: bool):
  app = web.Application(middlewares=[cors_middleware])
  app['streams'] = dict()
  app['debug'] = debug
  app.on_shutdown.append(on_shutdown)

  app.router.add_post("/stream", get_stream)
  app.router.add_get("/schema", get_schema)

  # Retry binding port
  for i in range(5):
    try:
      web.run_app(app, host=host, port=port)
      break
    except OSError:
      print(f"Port {port} busy, retrying...")
      time.sleep(1)


def main():
  parser = argparse.ArgumentParser(description="WebRTC daemon")
  parser.add_argument("--host", type=str, default="0.0.0.0")
  parser.add_argument("--port", type=int, default=5001)
  parser.add_argument("--debug", action="store_true")
  args = parser.parse_args()
  webrtcd_thread(args.host, args.port, args.debug)


if __name__ == "__main__":
  main()
