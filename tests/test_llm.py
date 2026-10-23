"""llm.py : nouvel essai après un plantage du moteur, file d'attente par priorité."""

from tests import _env  # noqa: F401  (données temporaires)

import threading
import time
import unittest
from unittest import mock

import ollama

import llm

CRASH = "llama-server process has terminated: exit status 0xc0000409: The system detected an overrun"


def crash():
    return ollama.ResponseError(CRASH, 500)


class FakeClient:
    """chat/embed qui échouent `fails` fois (plantage du moteur) puis répondent."""

    def __init__(self, fails=0, error=crash, chunks=("a", "b"), fail_after=None):
        self.fails, self.error, self.chunks, self.fail_after = fails, error, chunks, fail_after
        self.calls = 0
        self.closed = 0

    def _fail(self):
        self.calls += 1
        if self.fails > 0:
            self.fails -= 1
            raise self.error()

    def chat(self, **kwargs):
        if kwargs.get("stream"):
            return self._stream()
        self._fail()
        return "réponse"

    def embed(self, **kwargs):
        self._fail()
        return "vecteurs"

    def _stream(self):
        try:
            self._fail()
            for i, c in enumerate(self.chunks):
                if self.fail_after is not None and i == self.fail_after:
                    raise crash()
                yield c
        finally:
            self.closed += 1


class RetryTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(llm, "RETRY_DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        llm.gate = llm.Gate()

    def test_call_retried_once_after_crash(self):
        client = FakeClient(fails=1)
        self.assertEqual(llm.chat("test", client=client, model="m", messages=[]), "réponse")
        self.assertEqual(client.calls, 2)

    def test_second_crash_raised(self):
        client = FakeClient(fails=2)
        with self.assertRaises(ollama.ResponseError):
            llm.embed("test", client=client, model="m", input=[])
        self.assertEqual(client.calls, 2)

    def test_other_errors_not_retried(self):
        client = FakeClient(fails=1, error=lambda: ollama.ResponseError("model 'x' not found", 404))
        with self.assertRaises(ollama.ResponseError):
            llm.chat("test", client=client, model="x", messages=[])
        self.assertEqual(client.calls, 1)

    def test_missing_runner_not_retried(self):
        self.assertFalse(llm.crashed(ollama.ResponseError("error starting llama-server: llama-server binary "
                                                          "not found (checked: C:\\…)", 500)))
        self.assertTrue(llm.crashed(crash()))
        self.assertTrue(llm.crashed(ollama.ResponseError("model runner has unexpectedly stopped, this may be "
                                                         "due to resource limitations", 500)))

    def test_stream_retried_before_first_chunk(self):
        client = FakeClient(fails=1)
        self.assertEqual(list(llm.chat("test", client=client, stream=True)), ["a", "b"])
        self.assertEqual(client.calls, 2)
        self.assertEqual(client.closed, 2)

    def test_stream_not_retried_once_started(self):
        client = FakeClient(fail_after=1)
        got = []
        with self.assertRaises(ollama.ResponseError):
            for c in llm.chat("test", client=client, stream=True):
                got.append(c)
        self.assertEqual(got, ["a"])          # rien en double
        self.assertEqual(client.calls, 1)

    def test_stream_close_releases_turn(self):
        client = FakeClient(chunks=("a", "b", "c"))
        stream = llm.chat("test", llm.BATCH, client=client, stream=True)
        self.assertEqual(next(stream), "a")
        self.assertEqual(len(llm.gate.snapshot()["running"]), 1)
        stream.close()
        self.assertEqual(llm.gate.snapshot()["running"], [])
        self.assertEqual(client.closed, 1)


class GateTest(unittest.TestCase):
    def setUp(self):
        self.gate = llm.Gate()

    def run_job(self, label, priority, order, hold=0.0, stop=None):
        def body():
            job = self.gate.acquire(label, priority, stop)
            order.append(label if job else f"{label} arrêté")
            time.sleep(hold)
            self.gate.release(job)
        t = threading.Thread(target=body, daemon=True)
        t.start()
        return t

    def test_interactive_never_waits(self):
        a = self.gate.acquire("fond", llm.BACKGROUND)
        t0 = time.monotonic()
        b = self.gate.acquire("question", llm.INTERACTIVE)
        self.assertLess(time.monotonic() - t0, 0.1)
        self.gate.release(a)
        self.gate.release(b)

    def test_background_waits_for_interactive(self):
        q = self.gate.acquire("question", llm.INTERACTIVE)
        order = []
        t = self.run_job("vecteurs", llm.BACKGROUND, order)
        time.sleep(0.7)
        self.assertEqual(order, [])
        self.assertEqual([j["label"] for j in self.gate.snapshot()["waiting"]], ["vecteurs"])
        self.gate.release(q)
        t.join(2)
        self.assertEqual(order, ["vecteurs"])

    def test_priority_then_arrival(self):
        hold = self.gate.acquire("vecteurs", llm.BACKGROUND)     # un appel de fond en cours
        order = []
        threads = [self.run_job("fond 2", llm.BACKGROUND, order)]
        time.sleep(0.1)
        threads.append(self.run_job("gmail 1", llm.BATCH, order))
        time.sleep(0.1)
        threads.append(self.run_job("gmail 2", llm.BATCH, order))
        time.sleep(0.1)
        threads.append(self.run_job("annonce", llm.ANNOUNCE, order))
        time.sleep(0.2)
        self.gate.release(hold)
        for t in threads:
            t.join(5)
        self.assertEqual(order, ["annonce", "gmail 1", "gmail 2", "fond 2"])

    def test_one_non_interactive_at_a_time(self):
        order, active, peak = [], [0], [0]
        lock = threading.Lock()

        def body(label):
            job = self.gate.acquire(label, llm.BATCH)
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            self.gate.release(job)
            order.append(label)

        threads = [threading.Thread(target=body, args=(f"e-mail {i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(peak[0], 1)
        self.assertEqual(len(order), 5)

    def test_announce_waits_at_most(self):
        q = self.gate.acquire("conversation", llm.INTERACTIVE)
        order = []
        with mock.patch.object(llm, "ANNOUNCE_WAIT", 0.6):
            t0 = time.monotonic()
            t = self.run_job("annonce", llm.ANNOUNCE, order)
            t.join(3)
        self.assertEqual(order, ["annonce"])
        self.assertGreaterEqual(time.monotonic() - t0, 0.5)
        self.gate.release(q)

    def test_stop_while_waiting(self):
        q = self.gate.acquire("question", llm.INTERACTIVE)
        stop = threading.Event()
        order = []
        t = self.run_job("vecteurs", llm.BACKGROUND, order, stop=stop)
        time.sleep(0.2)
        stop.set()
        t.join(2)
        self.assertEqual(order, ["vecteurs arrêté"])
        self.assertEqual(self.gate.snapshot()["waiting"], [])
        self.gate.release(q)

    def test_stream_stopped_while_waiting_yields_nothing(self):
        llm.gate = self.gate
        q = self.gate.acquire("question", llm.INTERACTIVE)
        stop = threading.Event()
        stop.set()
        client = FakeClient()
        self.assertEqual(list(llm.chat("gmail", llm.BATCH, client=client, stop=stop, stream=True)), [])
        self.assertEqual(client.calls, 0)
        self.gate.release(q)

    def test_change_notifications_grouped(self):
        sent = []
        self.gate.on_change = sent.append
        for _ in range(10):
            self.gate.release(self.gate.acquire("vecteurs", llm.BACKGROUND))
        job = self.gate.acquire("conversation", llm.INTERACTIVE)
        time.sleep(0.6)
        self.assertEqual(len(sent), 1)
        self.assertEqual([j["label"] for j in sent[0]["running"]], ["conversation"])
        self.gate.release(job)
        time.sleep(0.6)
        self.assertEqual(sent[-1]["running"], [])


if __name__ == "__main__":
    unittest.main()
