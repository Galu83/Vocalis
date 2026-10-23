"""Met en pause la musique et les vidéos (YouTube, Spotify…) pendant que Vocalis
parle, puis les relance. Seuls les médias que Vocalis a lui-même mis en pause sont
relancés. Passe par les contrôles multimédias de Windows (ceux des touches
lecture/pause du clavier)."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

log = logging.getLogger(__name__)

RESUME_DELAY_S = 1.2       # une nouvelle lecture juste après n'entend pas la musique repartir


class MediaPauser:
    def __init__(self, enabled: Callable[[], bool]):
        self.enabled = enabled
        self._holders: set[str] = set()
        self._paused: list = []            # sessions mises en pause par Vocalis
        self._gen = 0
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="medias")

    def hold(self, who: str) -> None:
        """Vocalis commence à parler."""
        with self._lock:
            first = not self._holders
            self._holders.add(who)
            self._gen += 1
        if first and self.enabled():
            self._pool.submit(self._run, self._pause)

    def release(self, who: str) -> None:
        """Vocalis a fini de parler : les médias repartent un peu après."""
        with self._lock:
            if who not in self._holders:
                return
            self._holders.discard(who)
            if self._holders:
                return
            self._gen += 1
            gen = self._gen
        threading.Timer(RESUME_DELAY_S, self._resume_if, args=(gen,)).start()

    def _resume_if(self, gen: int) -> None:
        with self._lock:
            if gen != self._gen or self._holders:
                return                     # Vocalis a repris la parole entre-temps
        self._pool.submit(self._run, self._resume)

    # ------------------------------------------------------------------ Windows
    @staticmethod
    def _run(coro_fn) -> None:
        import asyncio
        try:
            asyncio.run(coro_fn())
        except Exception as exc:
            log.debug("Contrôle des médias impossible : %s", exc)

    async def _pause(self) -> None:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status)
        mgr = await Manager.request_async()
        for s in mgr.get_sessions():
            try:
                info = s.get_playback_info()
                if info is None or info.playback_status != Status.PLAYING:
                    continue
                if await s.try_pause_async():
                    self._paused.append(s)
                    log.info("Média mis en pause : %s", s.source_app_user_model_id)
            except Exception as exc:
                log.debug("Pause impossible : %s", exc)

    async def _resume(self) -> None:
        paused, self._paused = self._paused, []
        for s in paused:
            try:
                await s.try_play_async()
                log.info("Média relancé : %s", s.source_app_user_model_id)
            except Exception as exc:
                log.debug("Reprise impossible : %s", exc)
