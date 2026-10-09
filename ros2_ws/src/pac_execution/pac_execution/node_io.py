"""Fresh native-world pose samples and bounded waits used by the ROS nodes."""

from collections import deque
from dataclasses import dataclass
import math
import threading
import time

from .contract import ExecutionFault, quaternion, vector


@dataclass(frozen=True)
class PoseSample:
    position: tuple
    orientation: tuple
    sequence: int
    received: float


def settled_sample(values, *, after, now, max_age, tolerance, window, angle_tolerance):
    recent = [s for s in values if s.sequence > after]
    if len(recent) < 3:
        return None
    recent = [s for s in recent if s.received >= recent[-1].received-window-.1]
    if len(recent) < 3 or recent[-1].received-recent[0].received < window:
        return None
    last = recent[-1]
    deviation = max(math.dist(s.position, last.position) for s in recent)
    # q and -q describe the same orientation. A spinning box with a fixed
    # centre is not a settled placement.
    rotation = max(2*math.acos(min(1., abs(sum(a*b for a, b in
                                        zip(s.orientation, last.orientation))))) for s in recent)
    if deviation <= tolerance and rotation <= angle_tolerance and now-last.received <= max_age:
        return last
    return None


class WorldPoses:
    def __init__(self, box_ids, *, max_age=1.0):
        self.ids, self.max_age = set(box_ids), max_age
        self.condition = threading.Condition()
        self.samples = {bid: deque(maxlen=256) for bid in self.ids}
        self.sequence = 0

    def feed(self, message):
        # Input must be the PRIVATE bridge from /world/<world>/pose/info.
        # Fortress supplies top-level model poses in world coordinates, with
        # model.name as child_frame_id. Link names are ignored. Pose_V's vector
        # timestamp is not preserved by the pinned TF converter, so freshness
        # uses arrival sequence + a monotonic receive-age check.
        with self.condition:
            self.sequence += 1
            now = time.monotonic()
            for tf in message.transforms:
                bid = tf.child_frame_id
                if bid not in self.ids:
                    continue
                if tf.header.frame_id not in ('', 'world'):
                    raise ExecutionFault('Native pose bridge is not in the world frame')
                p, q = tf.transform.translation, tf.transform.rotation
                self.samples[bid].append(PoseSample(vector((p.x, p.y, p.z), 3, 'world pose'),
                                                    quaternion((q.x, q.y, q.z, q.w)),
                                                    self.sequence, now))
            self.condition.notify_all()

    def get(self, bid, *, after=-1):
        with self.condition:
            values = self.samples[bid]
            if not values or values[-1].sequence <= after or time.monotonic()-values[-1].received > self.max_age:
                raise ExecutionFault('Fresh actual world pose is unavailable: '+bid)
            return values[-1]

    def stable(self, bid, *, after=-1, timeout=8.0, tolerance=.002, window=.25,
               angle_tolerance=math.radians(1)):
        deadline = time.monotonic()+timeout
        with self.condition:
            while time.monotonic() < deadline:
                last = settled_sample(self.samples[bid], after=after, now=time.monotonic(),
                                      max_age=self.max_age, tolerance=tolerance,
                                      window=window, angle_tolerance=angle_tolerance)
                if last is not None:
                    return last
                self.condition.wait(min(.1, max(0., deadline-time.monotonic())))
        raise ExecutionFault('Box did not produce fresh settled measurements: '+bid)


def wait_future(future, timeout, label):
    event = threading.Event()
    future.add_done_callback(lambda _: event.set())
    if not event.wait(timeout):
        raise ExecutionFault(label+' timed out')
    if future.exception() is not None:
        raise ExecutionFault(label+': '+str(future.exception()))
    return future.result()
