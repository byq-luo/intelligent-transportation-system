import cv2
from enum import Enum, unique

from LPR import lpr


@unique
class CarDir(Enum):
    STOP = 0
    FOREWORD = 1
    LEFT = 2
    RIGHT = 3

class MyQueue:
    def __init__(self, max_size=10):
        self.max_size = max_size
        # The element in array is center point
        # There is no need to set front and rear pointer
        self.array = [[-1, -1]] * max_size
        self.index = 0

    def push(self, data):
        self.array[self.index] = data
        self.index = (self.index + 1) % self.max_size

    def get(self):
        ret = self.array[(self.index - 1) % self.max_size]
        return ret if ret != [-1, -1] else None

    def get_2_elements(self):
        ret1 = self.array[(self.index - 1) % self.max_size]
        if ret1 == [-1, -1]:
            ret1 = None
        ret2 = self.array[(self.index - 2) % self.max_size]
        if ret2 == [-1, -1]:
            ret2 = None
        return ret1, ret2


class Bbox:
    def __init__(self, tlwh):
        self.x1, self.y1, self.w, self.h = tlwh
        self.x2, self.y2 = self.x1 + self.w, self.y1 + self.h
        self.center_x = int(self.x1 + self.w / 2)
        self.center_y = int(self.y1 + self.h / 2)
        self.center = [self.center_x, self.center_y]
        self.center_numpy = [self.center_y, self.center_x]
    

class Track:
    def __init__(self, obj_id, category, tlwh, confidence, roi):
        self.obj_id = obj_id
        self.category = category
        self.tlwh = tlwh
        self.confidence = confidence
        self.roi = roi

        self.bbox = Bbox(tlwh)
        self.rect = ((self.bbox.center_x, self.bbox.center_y),
                     (self.bbox.w, self.bbox.h), 0)   # The track is a rect without rotation


class Car(Track):
    def __init__(self, obj_id, tlwh, confidence, roi, lanes):
        super().__init__(obj_id, "car", tlwh, confidence, roi)

        self.license_plate = None
        self.license_confidence = 0

        self.belong_lane = None
        self.allow_direction = None
        self.set_allow_direction(lanes)

        # self.speed = 0    # TODO
        self.is_moving = False
        self.direction = CarDir.STOP
        self.history_center = MyQueue(max_size=10)

        self.is_crossing_line = False
        self.not_wait_for_person = False
        self.drive_without_guidance = False
        self.run_the_red_light = False

    def update(self):
        self.set_license()
        self.set_is_moving()
        self.set_moving_dir()
        self.set_crossing_line()
        self.set_run_the_red_light()
        self.set_drive_without_guidance()

    def set_allow_direction(self, lanes):
        if self.allow_direction is None:
            for lane in lanes:
                if is_point_in_quad(self.bbox.center_numpy, lane.boundary):
                    self.belong_lane = lane
                    if lane.category == "left":
                        self.allow_direction = [CarDir.LEFT]
                    elif lane.category == "right":
                        self.allow_direction = [CarDir.RIGHT]
                    elif lane.category == "straight":
                        self.allow_direction = [CarDir.FOREWORD]
                    elif lane.category == "straight_left":
                        self.allow_direction = [CarDir.LEFT, CarDir.FOREWORD]
                    elif lane.category == "straight_right":
                        self.allow_direction = [CarDir.RIGHT, CarDir.FOREWORD]
                    break

    def set_license(self):
        license_plate, license_confidence = lpr.license_plate_recognize(self.roi)
        if license_confidence > self.license_confidence:
            self.license_confidence = license_confidence
            self.license_plate = license_plate

    def set_is_moving(self):
        p1, p2 = self.history_center.get_2_elements()
        self.is_moving = False if p1 is None or p2 is None or p1 == p2 else True

    def set_moving_dir(self):
        if self.is_moving:
            p_new, p_old = self.history_center.get_2_elements()
            x1, y1 = p_new
            x2, y2 = p_old

            # Moving to the Upper side
            if y1 > y2:
                k = (float(y1) - float(y2)) / (float(x1) - float(x2))
                if 0 <= k < 1:
                    self.direction = CarDir.RIGHT
                elif k >= 1 or k <= -1:
                    self.direction = CarDir.FOREWORD
                elif -1 <= k < 0:
                    self.direction = CarDir.LEFT

    def set_crossing_line(self):
        left_top, right_top = self.belong_lane.boundary.left_top, self.belong_lane.boundary.right_top
        x1, y1 = left_top
        x2, y2 = right_top

        k = (float(y2) - float(y1)) / (float(x2) - float(x1))
        m = (float(x2) * float(y1) - float(x1) * float(y2)) / (float(x2) - float(x1))

        self.is_crossing_line = (self.bbox.center_y < self.bbox.center_x * k + m) and (not self.is_moving)

    def set_not_wait_for_person(self, person_list, crossing_rect):
        if cv2.rotatedRectangleIntersection(self.rect, crossing_rect)[0] == cv2.INTERSECT_NONE:
            self.not_wait_for_person = False
        else:
            for person in person_list:
                if cv2.rotatedRectangleIntersection(person.rect, crossing_rect)[0] != cv2.INTERSECT_NONE:
                    self.not_wait_for_person = True
                    return
            self.not_wait_for_person = False

    def set_run_the_red_light(self):
        # Previously confirm the red light environment
        self.run_the_red_light = True if self.is_moving and self.direction == CarDir.FOREWORD else False

    def set_drive_without_guidance(self):
        if self.allow_direction is not None:
            self.drive_without_guidance = True if self.direction == self.allow_direction else False


class Person(Track):
    def __init__(self, obj_id, tlwh, confidence):
        # The person object doesn't need roi
        super().__init__(obj_id, "person", tlwh, confidence, None)


def is_point_in_quad(point, quad):
    # Using cross product to see if the point are in the same direction of lines
    x, y = point

    x1, y1 = quad.left_top
    x2, y2 = quad.right_top
    x3, y3 = quad.right_bottom
    x4, y4 = quad.left_bottom

    a = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
    b = (x3 - x2) * (y - y2) - (y3 - y2) * (x - x2)
    c = (x4 - x3) * (y - y3) - (y4 - y3) * (x - x3)
    d = (x1 - x4) * (y - y4) - (y1 - y4) * (x - x4)

    return (a > 0 and b > 0 and c > 0 and d > 0) or (a < 0 and b < 0 and c < 0 and d < 0)


if __name__ == "__main__":
    rect1 = [[]]