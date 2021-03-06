import pygame

import numpy as np
from deep_logistics.action_space import ActionSpace
from deep_logistics.grid import Grid


class Agent:
    id = 0

    MAX_SPEED = 750  # X centimeters per second is max speed
    MAX_THRUST = 2

    IDLE = 0
    MOVING = 1
    PICKUP = 2
    DELIVERY = 3
    DESTROYED = 4
    INACTIVE = 5

    ALL_STATES = [IDLE, MOVING, PICKUP, DELIVERY, DESTROYED, INACTIVE]

    IMMOBILE_STATES = [DESTROYED, INACTIVE]

    @staticmethod
    def new_id():
        _id = Agent.id
        Agent.id += 1
        return _id

    def __init__(self, env):
        self.environment = env
        self.id = Agent.new_id()
        self._cell = None
        self.speed = 0
        self.sensor_radius = 2
        r = self.sensor_radius

        self.proximity_coordinates = [
            (x, y) for y in range(-r, r + 1) for x in range(-r, r + 1) if x != 0 and y == 0 or y != 0 and x == 0
        ]

        self.task = None

        self.state = Agent.INACTIVE  # TODO

        self.action = None
        self.action_intensity = 0  # Distance moved in the direction
        self.action_progress = 0  # Accumulator for progress

        if self.environment.taxi_control == "constant":
            self.AGENT_ACCELERATION = 1.0
            self.AGENT_DEACCELERATION = 1.0
            self.AGENT_MAX_SPEED = 1.0
        elif self.environment.taxi_control == "constant_acceleration":
            self.AGENT_ACCELERATION = 0.33
            self.AGENT_DEACCELERATION = 0.25
            self.AGENT_MAX_SPEED = 1.0
        else:
            raise NotImplementedError("The taxi_control state %s is not implemented!" % self.environment.taxi_control)

        self.total_deliveries = 0
        self.total_pickups = 0
        self.total_actions = 0

    def reset_stats(self):
        self.total_deliveries = 0
        self.total_pickups = 0
        self.total_actions = 0

    @property
    def cell(self):
        """Ensure consistency between grid and agent."""
        if not self._cell:
            return None

        assert self._cell.occupant == self or self._cell.occupant is None
        return self._cell

    @cell.setter
    def cell(self, x):

        if self.cell:
            self.cell.occupant = None

        self._cell = x

    def spawn(self, spawn_point):
        result = self.environment.grid.move(self, spawn_point.x, spawn_point.y)
        assert result == Grid.MOVE_OK
        self.state = Agent.IDLE
        self.reset_stats()

    def despawn(self):
        self.reset_action()
        self.cell = None
        self.state = Agent.INACTIVE

    def crash(self):

        if self.task:
            self.task.abort()
        self.task = None

        self.reset_action()
        self.cell = None

        self.state = Agent.DESTROYED

    def request_task(self):
        if self.state not in Agent.IMMOBILE_STATES:
            self.environment.scheduler.give_task(self)

    def is_terminal(self):
        return self.state in Agent.IMMOBILE_STATES

    def reset_action(self):
        self.action = None
        self.action_intensity = 0

    def automate(self):
        return None

    def do_action(self, action):
        self.total_actions += 1

        if self.is_terminal():
            return

        if action < 0 or action >= ActionSpace.N_ACTIONS:
            raise ValueError("The inserted action is out of action_space bounds 0 => %s." % ActionSpace.N_ACTIONS)

        """Ensure that action is Integer"""
        action = int(action)

        if action is ActionSpace.NOOP:
            return

        if self.action is None:
            self.action = action

        if self.action != action and self.state == Agent.IDLE:
            self.action = action

        elif self.action != action:
            #self._decrease_acceleration()
            pass
        elif action == self.action:
            self._increase_acceleration()

    def update(self):

        if self.state is Agent.INACTIVE:
            """Inactive state - Means the agent has not spawned yet, and cannot be updated."""
            return
        elif self.state is Agent.DESTROYED:
            """Destroyed state - Means the agent should be set to inactive 
            (Algorithms should have catched the destroyed state)."""
            self.state = Agent.INACTIVE
            return
        elif self.action is None:
            """If the player has no action at all (Means that the agent is fully de-accelerated and action is unset)."""
            return

        action = self.action

        d_prog = ((self.action_intensity * Agent.MAX_SPEED) / Agent.MAX_SPEED) * self.environment.tick_ps_ratio
        self.action_progress += d_prog

        """Calculate number of steps to tage based on the progress"""
        steps = int(self.action_progress)
        self.action_progress -= steps

        assert self.action_progress < 1  # TODO - Remove when release

        x, y = np.multiply(ActionSpace.DIRECTIONS[action], steps)

        return_code = self.environment.grid.move_relative(self, x, y)
        self.state = Agent.MOVING

        if return_code == Grid.MOVE_WALL_COLLISION:
            self.crash()
            return
        elif return_code == Grid.MOVE_AGENT_COLLISION:
            # TODO additional handling for other agent
            self.environment.grid.relative_cell(self, x, y).occupant.crash()
            self.crash()
            
            return

        assert action == self.action
        self._decrease_acceleration()

        if self.action_intensity == 0:
            self.state = Agent.IDLE

    def _decrease_acceleration(self):
        """Decay acceleration / Thrust."""
        self.action_intensity = max(0.0, self.action_intensity - self.AGENT_DEACCELERATION)

    def _increase_acceleration(self):
        self.action_intensity = min(1.0, self.action_intensity + self.AGENT_ACCELERATION)

    def get_proximity_sensors(self):
        left = 1000
        right = 1000
        up = 1000
        down = 1000

        if self.cell:
            r = 2  # Range

            for x, y in self.proximity_coordinates:
                try:
                    has_occupant = self.environment.grid.relative_cell(self, x, y).occupant is not None
                except: # TODO maybe this should be handled with IF STATEMENT?
                    has_occupant = False

                """Left proximity"""
                """Right proximity"""
                """Up proximity"""
                """Down proximity"""
                if x < 0 and y == 0 and has_occupant:
                    left = min(left, abs(x))
                elif x > 0 and y == 0 and has_occupant:
                    right = min(right, x)
                elif y < 0 and x == 0 and has_occupant:
                    up = min(up, abs(y))
                elif y > 0 and x == 0 and has_occupant:
                    down = min(down, y)

        if left == 1000:
            left = -1
        if right == 1000:
            right = -1
        if up == 1000:
            up = -1
        if down == 1000:
            down = -1

        return [left, right, up, down]


class InputAgent(Agent):

    def __init__(self, env):
        super().__init__(env)
        self._cb = []

    def add_event_callback(self, cb):
        self._cb.append(cb)

    def automate(self):
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_LEFT:
                    self.do_action(ActionSpace.LEFT)
                if event.key == pygame.K_RIGHT:
                    self.do_action(ActionSpace.RIGHT)
                if event.key == pygame.K_DOWN:
                    self.do_action(ActionSpace.DOWN)
                if event.key == pygame.K_UP:
                    self.do_action(ActionSpace.UP)
                if event.key == pygame.K_KP_ENTER:
                    self.do_action(ActionSpace.NOOP)
                for c in self._cb:
                    c()


class ManhattanAgent(Agent):

    def __init__(self, env):
        super().__init__(env)

    def automate(self, perform_action=True):
        action = ActionSpace.NOOP
        if self.task:
            # +dY = Above
            # -dY = Below
            # +dX = Right Of
            # -dX = Left Of
            task_coords = self.task.get_coordinates()

            d_x = self.cell.x - task_coords.x
            d_y = self.cell.y - task_coords.y

            is_aligned_x = d_x == 0
            is_aligned_y = d_y == 0

            if not is_aligned_x:
                if d_x > 0:
                    action = ActionSpace.LEFT
                else:
                    action = ActionSpace.RIGHT
            elif not is_aligned_y:
                if d_y > 0:
                    action = ActionSpace.UP
                else:
                    action = ActionSpace.DOWN

        if perform_action:
            self.do_action(action)

        return action
            #print("x=%s | y=%s | dX=%s | dY=%s | Thrust=%s | alignment_x=%s | alignment_y=%s" %
            #      (self.cell.x, self.cell.y, d_x, d_y, self.action_intensity, is_aligned_x, is_aligned_y))
