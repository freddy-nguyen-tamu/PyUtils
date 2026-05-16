import math
import random
import sys
from dataclasses import dataclass

import pygame

# ============================================================
# LASER JUMP SURVIVAL
# One player only
#
# Controls:
#   WASD / Arrow Keys = move
#   SPACE             = jump (invincible while airborne)
#   R                 = restart after death
# ============================================================

pygame.init()

# -----------------------------
# Window / timing
# -----------------------------
WIDTH, HEIGHT = 1100, 760
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Laser Jump Survival")
clock = pygame.time.Clock()

# -----------------------------
# Fonts
# -----------------------------
font = pygame.font.SysFont("arial", 24)
small_font = pygame.font.SysFont("arial", 18)
big_font = pygame.font.SysFont("arial", 56, bold=True)

# -----------------------------
# Arena
# -----------------------------
CENTER = pygame.Vector2(WIDTH // 2, HEIGHT // 2 + 20)
ARENA_RADIUS = 280

# -----------------------------
# Colors
# -----------------------------
BG_COLOR = (10, 12, 18)
ARENA_FILL = (28, 33, 42)
ARENA_RING = (95, 110, 130)
GRID_COLOR = (48, 55, 68)
TEXT_COLOR = (240, 240, 240)

RED = (255, 70, 70)
YELLOW = (255, 230, 80)
GREEN = (110, 255, 110)
CYAN = (100, 210, 255)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# Character colors
SKIN = (255, 220, 180)
HAIR = (40, 28, 20)
SHIRT = (85, 170, 255)
PANTS = (45, 55, 105)
SHOE = (25, 25, 30)
INVINCIBLE_GLOW = (255, 255, 130)

# -----------------------------
# Player settings
# -----------------------------
PLAYER_SPEED = 280.0
JUMP_HEIGHT = 95.0
JUMP_DURATION = 0.78

MAX_HEALTH = 100
LASER_DAMAGE = 20
DAMAGE_COOLDOWN = 0.55

# -----------------------------
# Laser settings
# -----------------------------
LASER_THICKNESS = 10
LASER_SPEED_MIN = 120.0
LASER_SPEED_MAX = 210.0

LASER_SPAWN_MARGIN = 40
LASER_DESPAWN_MARGIN = 100

SPAWN_INTERVAL_START = 1.25
SPAWN_INTERVAL_MIN = 0.48

# -----------------------------
# Helpers
# -----------------------------
def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def point_line_distance(point: pygame.Vector2, line_point: pygame.Vector2, normal: pygame.Vector2) -> float:
    return abs((point - line_point).dot(normal))


def segment_inside_circle(center: pygame.Vector2, radius: float, normal: pygame.Vector2, offset: float):
    tangent = pygame.Vector2(-normal.y, normal.x)
    inside = radius * radius - offset * offset
    if inside <= 0:
        return None

    half_len = math.sqrt(inside)
    mid = center + normal * offset
    p1 = mid + tangent * half_len
    p2 = mid - tangent * half_len
    return p1, p2


# ============================================================
# LASER
# ============================================================
@dataclass
class Laser:
    angle: float
    offset: float
    speed: float
    color: tuple
    thickness: int = LASER_THICKNESS

    @property
    def normal(self) -> pygame.Vector2:
        return pygame.Vector2(math.cos(self.angle), math.sin(self.angle))

    def update(self, dt: float) -> None:
        self.offset += self.speed * dt

    def is_dead(self) -> bool:
        return abs(self.offset) > ARENA_RADIUS + LASER_DESPAWN_MARGIN

    def collides_with_player(self, player_pos: pygame.Vector2, player_radius: float, invincible: bool) -> bool:
        if invincible:
            return False

        line_point = CENTER + self.normal * self.offset
        dist = point_line_distance(player_pos, line_point, self.normal)
        return dist <= (self.thickness / 2 + player_radius)

    def draw(self, surface: pygame.Surface) -> None:
        chord = segment_inside_circle(CENTER, ARENA_RADIUS, self.normal, self.offset)
        if chord is None:
            return

        p1, p2 = chord

        glow = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        pygame.draw.line(glow, (*self.color, 35), p1, p2, self.thickness * 7)
        pygame.draw.line(glow, (*self.color, 80), p1, p2, self.thickness * 3)
        surface.blit(glow, (0, 0))

        pygame.draw.line(surface, self.color, p1, p2, self.thickness)
        inner = tuple(min(255, c + 40) for c in self.color)
        pygame.draw.line(surface, inner, p1, p2, max(2, self.thickness // 3))


# ============================================================
# PLAYER
# ============================================================
class Player:
    def __init__(self) -> None:
        self.pos = pygame.Vector2(CENTER.x, CENTER.y)
        self.height = 0.0
        self.is_jumping = False
        self.jump_timer = 0.0
        self.invincible = False

        self.health = MAX_HEALTH
        self.damage_cooldown = 0.0

        self.body_radius = 18
        self.hit_radius = 20

        self.run_anim = 0.0
        self.facing = 1  # 1 right, -1 left

    def reset(self) -> None:
        self.pos = pygame.Vector2(CENTER.x, CENTER.y)
        self.height = 0.0
        self.is_jumping = False
        self.jump_timer = 0.0
        self.invincible = False
        self.health = MAX_HEALTH
        self.damage_cooldown = 0.0
        self.run_anim = 0.0
        self.facing = 1

    def start_jump(self) -> None:
        if not self.is_jumping:
            self.is_jumping = True
            self.jump_timer = 0.0
            self.invincible = True

    def take_damage(self, amount: int) -> None:
        if self.invincible or self.damage_cooldown > 0:
            return
        self.health = max(0, self.health - amount)
        self.damage_cooldown = DAMAGE_COOLDOWN

    def update(self, dt: float, keys) -> None:
        move = pygame.Vector2(0, 0)

        if keys[pygame.K_w] or keys[pygame.K_UP]:
            move.y -= 1
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            move.y += 1
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            move.x -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            move.x += 1

        moving = move.length_squared() > 0
        if moving:
            move = move.normalize() * PLAYER_SPEED * dt
            self.pos += move
            self.run_anim += dt * 10

            if move.x > 0.05:
                self.facing = 1
            elif move.x < -0.05:
                self.facing = -1
        else:
            self.run_anim += dt * 2

        # stay inside arena
        offset = self.pos - CENTER
        dist = offset.length()
        max_dist = ARENA_RADIUS - self.hit_radius - 8
        if dist > max_dist:
            self.pos = CENTER + offset.normalize() * max_dist

        # jump arc
        if self.is_jumping:
            self.jump_timer += dt
            t = self.jump_timer / JUMP_DURATION
            if t >= 1.0:
                self.is_jumping = False
                self.height = 0.0
                self.invincible = False
            else:
                # high arc
                self.height = 4 * JUMP_HEIGHT * t * (1 - t)
        else:
            self.height = 0.0
            self.invincible = False

        if self.damage_cooldown > 0:
            self.damage_cooldown -= dt
            if self.damage_cooldown < 0:
                self.damage_cooldown = 0

    def is_dead(self) -> bool:
        return self.health <= 0

    def draw_health_bar(self, surface: pygame.Surface) -> None:
        x, y = 22, 22
        w, h = 260, 24

        pygame.draw.rect(surface, (35, 35, 45), (x, y, w, h), border_radius=8)
        fill_w = int((self.health / MAX_HEALTH) * w)

        if self.health > 60:
            color = GREEN
        elif self.health > 30:
            color = YELLOW
        else:
            color = RED

        pygame.draw.rect(surface, color, (x, y, fill_w, h), border_radius=8)
        pygame.draw.rect(surface, WHITE, (x, y, w, h), 2, border_radius=8)

        label = font.render(f"Health: {self.health}/{MAX_HEALTH}", True, TEXT_COLOR)
        surface.blit(label, (x, y + 32))

    def draw(self, surface: pygame.Surface) -> None:
        floor_x = int(self.pos.x)
        floor_y = int(self.pos.y)
        draw_x = int(self.pos.x)
        draw_y = int(self.pos.y - self.height)

        # shadow
        shadow_radius_x = 24
        shadow_radius_y = max(8, int(12 - self.height * 0.04))
        shadow = pygame.Surface((120, 60), pygame.SRCALPHA)
        pygame.draw.ellipse(
            shadow,
            (0, 0, 0, 90),
            (60 - shadow_radius_x, 30 - shadow_radius_y, shadow_radius_x * 2, shadow_radius_y * 2),
        )
        surface.blit(shadow, (floor_x - 60, floor_y - 30))

        # invincible glow
        if self.invincible:
            glow = pygame.Surface((140, 140), pygame.SRCALPHA)
            pygame.draw.circle(glow, (*INVINCIBLE_GLOW, 70), (70, 70), 38)
            pygame.draw.circle(glow, (*INVINCIBLE_GLOW, 35), (70, 70), 50)
            surface.blit(glow, (draw_x - 70, draw_y - 70))

        # hurt flash
        flash = self.damage_cooldown > 0 and int(self.damage_cooldown * 18) % 2 == 0

        # simple run animation
        leg_swing = math.sin(self.run_anim * 1.8) * 8
        arm_swing = math.sin(self.run_anim * 1.8) * 7

        # during jump, tuck legs slightly
        if self.is_jumping:
            leg_swing *= 0.35
            arm_swing *= 0.35

        # torso anchor
        torso_top = (draw_x, draw_y - 32)
        torso_bottom = (draw_x, draw_y - 4)

        # legs
        left_hip = (draw_x - 6, draw_y - 4)
        right_hip = (draw_x + 6, draw_y - 4)

        left_knee = (draw_x - 8 - leg_swing * 0.5, draw_y + 10)
        right_knee = (draw_x + 8 + leg_swing * 0.5, draw_y + 10)

        left_foot = (draw_x - 10 + leg_swing, draw_y + 26 - (10 if self.is_jumping else 0))
        right_foot = (draw_x + 10 - leg_swing, draw_y + 26 - (10 if self.is_jumping else 0))

        # arms
        left_shoulder = (draw_x - 10, draw_y - 26)
        right_shoulder = (draw_x + 10, draw_y - 26)

        left_hand = (draw_x - 20 - arm_swing, draw_y - 6)
        right_hand = (draw_x + 20 + arm_swing, draw_y - 6)

        limb_color = SHIRT if not flash else RED

        pygame.draw.line(surface, PANTS, left_hip, left_knee, 5)
        pygame.draw.line(surface, PANTS, right_hip, right_knee, 5)
        pygame.draw.line(surface, SHOE, left_knee, left_foot, 5)
        pygame.draw.line(surface, SHOE, right_knee, right_foot, 5)

        pygame.draw.line(surface, limb_color, left_shoulder, left_hand, 5)
        pygame.draw.line(surface, limb_color, right_shoulder, right_hand, 5)

        # torso
        torso_rect = pygame.Rect(0, 0, 24, 30)
        torso_rect.center = (draw_x, draw_y - 18)
        pygame.draw.rounded_rect = getattr(pygame.draw, "rect")
        pygame.draw.rect(surface, SHIRT if not flash else RED, torso_rect, border_radius=8)

        # neck
        pygame.draw.line(surface, SKIN, (draw_x, draw_y - 34), (draw_x, draw_y - 30), 4)

        # head
        head_radius = 12
        head_center = (draw_x, draw_y - 46)
        pygame.draw.circle(surface, SKIN if not flash else (255, 170, 170), head_center, head_radius)

        # hair
        pygame.draw.circle(surface, HAIR, (draw_x, draw_y - 50), 11)
        pygame.draw.rect(surface, HAIR, (draw_x - 11, draw_y - 50, 22, 7), border_radius=4)

        # eyes
        eye_dx = 4 * self.facing
        pygame.draw.circle(surface, BLACK, (draw_x - 4 + eye_dx // 4, draw_y - 47), 2)
        pygame.draw.circle(surface, BLACK, (draw_x + 4 + eye_dx // 4, draw_y - 47), 2)

        # mouth
        pygame.draw.line(surface, BLACK, (draw_x - 3, draw_y - 41), (draw_x + 3, draw_y - 41), 1)

        # outline ring around character position
        if self.invincible:
            pygame.draw.circle(surface, WHITE, (draw_x, draw_y - 18), 34, 2)


# ============================================================
# GAME
# ============================================================
class Game:
    def __init__(self) -> None:
        self.player = Player()
        self.lasers = []
        self.spawn_timer = 0.0
        self.spawn_interval = SPAWN_INTERVAL_START
        self.time_alive = 0.0
        self.game_over = False

    def reset(self) -> None:
        self.player.reset()
        self.lasers.clear()
        self.spawn_timer = 0.0
        self.spawn_interval = SPAWN_INTERVAL_START
        self.time_alive = 0.0
        self.game_over = False

    def spawn_single_laser(self) -> None:
        angle = random.uniform(0, math.pi)
        side = random.choice([-1, 1])
        color = random.choice([RED, YELLOW, GREEN, (255, 120, 80), (140, 255, 150)])

        self.lasers.append(
            Laser(
                angle=angle,
                offset=side * -(ARENA_RADIUS + LASER_SPAWN_MARGIN),
                speed=random.uniform(LASER_SPEED_MIN, LASER_SPEED_MAX) * side,
                color=color,
            )
        )

    def spawn_crossing_lasers(self) -> None:
        base_angle = random.uniform(0, math.pi)
        side_a = random.choice([-1, 1])
        side_b = -side_a

        self.lasers.append(
            Laser(
                angle=base_angle,
                offset=side_a * -(ARENA_RADIUS + LASER_SPAWN_MARGIN),
                speed=random.uniform(LASER_SPEED_MIN, LASER_SPEED_MAX) * side_a,
                color=YELLOW,
            )
        )
        self.lasers.append(
            Laser(
                angle=(base_angle + random.uniform(0.55, 1.15)) % math.pi,
                offset=side_b * -(ARENA_RADIUS + LASER_SPAWN_MARGIN),
                speed=random.uniform(LASER_SPEED_MIN, LASER_SPEED_MAX) * side_b,
                color=RED,
            )
        )

    def update(self, dt: float) -> None:
        if self.game_over:
            return

        self.time_alive += dt
        difficulty = min(1.0, self.time_alive / 45.0)
        self.spawn_interval = SPAWN_INTERVAL_START - (SPAWN_INTERVAL_START - SPAWN_INTERVAL_MIN) * difficulty

        keys = pygame.key.get_pressed()
        self.player.update(dt, keys)

        self.spawn_timer += dt
        if self.spawn_timer >= self.spawn_interval:
            self.spawn_timer = 0.0
            if random.random() < 0.42:
                self.spawn_crossing_lasers()
            else:
                self.spawn_single_laser()

        for laser in self.lasers:
            laser.update(dt)

        self.lasers = [laser for laser in self.lasers if not laser.is_dead()]

        for laser in self.lasers:
            if laser.collides_with_player(self.player.pos, self.player.hit_radius, self.player.invincible):
                self.player.take_damage(LASER_DAMAGE)

        if self.player.is_dead():
            self.game_over = True

    def draw_background(self, surface: pygame.Surface) -> None:
        surface.fill(BG_COLOR)

        # arena fill
        pygame.draw.circle(surface, ARENA_FILL, CENTER, ARENA_RADIUS)
        pygame.draw.circle(surface, ARENA_RING, CENTER, ARENA_RADIUS, 4)

        # grid rings
        for r in range(56, ARENA_RADIUS, 56):
            pygame.draw.circle(surface, GRID_COLOR, CENTER, r, 1)

        # cross lines
        pygame.draw.line(
            surface,
            GRID_COLOR,
            (CENTER.x - ARENA_RADIUS, CENTER.y),
            (CENTER.x + ARENA_RADIUS, CENTER.y),
            1,
        )
        pygame.draw.line(
            surface,
            GRID_COLOR,
            (CENTER.x, CENTER.y - ARENA_RADIUS),
            (CENTER.x, CENTER.y + ARENA_RADIUS),
            1,
        )

        # outer vignette
        vignette = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        pygame.draw.circle(vignette, (255, 255, 255, 6), (int(CENTER.x), int(CENTER.y)), ARENA_RADIUS + 10, 8)
        surface.blit(vignette, (0, 0))

    def draw_ui(self, surface: pygame.Surface) -> None:
        self.player.draw_health_bar(surface)

        t1 = font.render(f"Time: {self.time_alive:.1f}s", True, TEXT_COLOR)
        t2 = font.render("Move: WASD / Arrows", True, TEXT_COLOR)
        t3 = font.render("Jump: SPACE", True, TEXT_COLOR)
        t4 = small_font.render("Jumping makes you invincible until you land.", True, (210, 210, 210))

        surface.blit(t1, (22, 88))
        surface.blit(t2, (22, 120))
        surface.blit(t3, (22, 150))
        surface.blit(t4, (22, 180))

        if self.player.invincible:
            inv = font.render("INVINCIBLE", True, INVINCIBLE_GLOW)
            surface.blit(inv, (22, 210))

        if self.player.damage_cooldown > 0 and not self.player.invincible:
            hit = font.render("HIT!", True, RED)
            surface.blit(hit, (22, 240))

        if self.game_over:
            over = big_font.render("YOU DIED", True, RED)
            restart = font.render("Press R to restart", True, WHITE)

            over_rect = over.get_rect(center=(WIDTH // 2, 120))
            restart_rect = restart.get_rect(center=(WIDTH // 2, 172))

            surface.blit(over, over_rect)
            surface.blit(restart, restart_rect)

    def draw(self, surface: pygame.Surface) -> None:
        self.draw_background(surface)

        for laser in self.lasers:
            laser.draw(surface)

        self.player.draw(surface)
        self.draw_ui(surface)


# ============================================================
# MAIN LOOP
# ============================================================
def main() -> None:
    game = Game()

    while True:
        dt = clock.tick(60) / 1000.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE and not game.game_over:
                    game.player.start_jump()
                elif event.key == pygame.K_r and game.game_over:
                    game.reset()

        game.update(dt)
        game.draw(screen)
        pygame.display.flip()


if __name__ == "__main__":
    main()