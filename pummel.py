import pygame
import random
import sys
import math

# ============================================================
# SETTINGS
# ============================================================

SCREEN_WIDTH = 1100
SCREEN_HEIGHT = 720

BOARD_WIDTH = 760
SIDE_PANEL_WIDTH = SCREEN_WIDTH - BOARD_WIDTH

FPS = 60

GRID_WIDTH = 22
GRID_HEIGHT = 14
PATH_LENGTH = 75

TILE_SIZE = 34
TILE_GAP = 4

MAX_HP = 30
CHEST_COST = 20
GOBLETS_TO_WIN = 3

MIN_DICE_ROLL = 1
MAX_DICE_ROLL = 10

# ============================================================
# COLORS
# ============================================================

BACKGROUND = (22, 24, 32)
PANEL_BG = (34, 37, 49)
PANEL_DARK = (25, 27, 36)
TEXT = (235, 235, 245)
MUTED_TEXT = (170, 175, 190)
BUTTON = (74, 92, 145)
BUTTON_HOVER = (96, 120, 190)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

TILE_COLORS = {
    "Key": (245, 198, 66),
    "Healing": (83, 207, 124),
    "Item": (91, 155, 255),
    "Reaper": (145, 96, 215),
    "Weapon Cache": (255, 126, 69),
    "White Skull": (225, 225, 225),
    "Event": (255, 85, 150),
    "Treasure Chest": (153, 94, 42),
}

PLAYER_COLORS = {
    "P": (95, 220, 255),
    "A": (255, 95, 95),
    "B": (150, 255, 110),
    "C": (255, 210, 90),
}

# ============================================================
# TILE AND ITEM DATA
# ============================================================

TILE_TYPES = {
    "Key": "K",
    "Healing": "H",
    "Item": "I",
    "Reaper": "R",
    "Weapon Cache": "W",
    "White Skull": "S",
    "Event": "E",
    "Treasure Chest": "T",
}

TILE_WEIGHTS = {
    "Key": 22,
    "Healing": 10,
    "Item": 16,
    "Reaper": 10,
    "Weapon Cache": 8,
    "White Skull": 12,
    "Event": 14,
    "Treasure Chest": 8,
}

NORMAL_ITEMS = [
    "Boxing Glove",
    "Shotgun",
    "Health Kit",
    "Bee Hive",
    "Magnet",
    "Eggplant",
    "Swap Portal",
    "Wrecking Ball",
    "Rocket Skewer",
    "Tactical Cactus Disguise",
    "Present",
    "Dark Summoning Staff",
    "Giga Laser",
    "Nuclear Waste Barrel",
    "Arcade Challenge",
]

POWERFUL_ITEMS = [
    "Wrecking Ball",
    "Rocket Skewer",
    "Dark Summoning Staff",
    "Giga Laser",
    "Nuclear Waste Barrel",
    "Arcade Challenge",
]

# ============================================================
# CLASSES
# ============================================================

class Player:
    def __init__(self, name, symbol, is_ai=False):
        self.name = name
        self.symbol = symbol
        self.is_ai = is_ai

        self.path_index = 0
        self.keys = 10
        self.hp = MAX_HP
        self.goblets = 0
        self.items = []

        self.cactus_protected = False
        self.skip_turns = 0
        self.poison_turns = 0

    def status_text(self):
        statuses = []

        if self.cactus_protected:
            statuses.append("Cactus")

        if self.poison_turns > 0:
            statuses.append(f"Poison {self.poison_turns}")

        if self.skip_turns > 0:
            statuses.append(f"Skip {self.skip_turns}")

        if not statuses:
            return "Normal"

        return ", ".join(statuses)


class Button:
    def __init__(self, rect, text, font, action=None):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.font = font
        self.action = action

    def draw(self, surface, mouse_pos):
        color = BUTTON_HOVER if self.rect.collidepoint(mouse_pos) else BUTTON
        pygame.draw.rect(surface, color, self.rect, border_radius=10)

        label = self.font.render(self.text, True, WHITE)
        label_rect = label.get_rect(center=self.rect.center)
        surface.blit(label, label_rect)

    def clicked(self, event):
        return event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(event.pos)


# ============================================================
# GAME
# ============================================================

class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Random 2D Board Game")

        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        self.clock = pygame.time.Clock()

        self.font_small = pygame.font.SysFont("arial", 16)
        self.font = pygame.font.SysFont("arial", 20)
        self.font_big = pygame.font.SysFont("arial", 30, bold=True)
        self.font_title = pygame.font.SysFont("arial", 38, bold=True)

        self.path = []
        self.tile_map = {}

        self.players = [
            Player("You", "P", is_ai=False),
            Player("AI 1", "A", is_ai=True),
            Player("AI 2", "B", is_ai=True),
            Player("AI 3", "C", is_ai=True),
        ]

        self.current_player_index = 0
        self.round_number = 1
        self.last_roll = None
        self.message_log = []
        self.game_over = False
        self.winner = None

        self.selected_item_index = None

        self.roll_button = Button(
            (BOARD_WIDTH + 35, 595, 140, 48),
            "Roll Dice",
            self.font,
            self.human_roll
        )

        self.use_item_button = Button(
            (BOARD_WIDTH + 190, 595, 140, 48),
            "Use Item",
            self.font,
            self.human_use_selected_item
        )

        self.new_game_button = Button(
            (BOARD_WIDTH + 35, 650, 295, 42),
            "New Game",
            self.font,
            self.reset_game
        )

        self.generate_board()
        self.add_message("Welcome! First to 3 goblets wins.")

    # ========================================================
    # MAP GENERATION
    # ========================================================

    def reset_game(self):
        self.players = [
            Player("You", "P", is_ai=False),
            Player("AI 1", "A", is_ai=True),
            Player("AI 2", "B", is_ai=True),
            Player("AI 3", "C", is_ai=True),
        ]

        self.current_player_index = 0
        self.round_number = 1
        self.last_roll = None
        self.message_log = []
        self.game_over = False
        self.winner = None
        self.selected_item_index = None

        self.generate_board()
        self.add_message("New game started.")

    def get_random_tile_type(self):
        names = list(TILE_WEIGHTS.keys())
        weights = list(TILE_WEIGHTS.values())
        return random.choices(names, weights=weights, k=1)[0]

    def get_random_direction(self):
        return random.choice([
            (0, 1),
            (0, -1),
            (-1, 0),
            (1, 0),
        ])

    def is_inside_map(self, x, y):
        return 0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT

    def generate_random_path(self):
        path = []

        x = GRID_WIDTH // 2
        y = GRID_HEIGHT // 2
        path.append((x, y))

        attempts = 0
        max_attempts = GRID_WIDTH * GRID_HEIGHT * 40

        while len(path) < PATH_LENGTH and attempts < max_attempts:
            attempts += 1

            dx, dy = self.get_random_direction()
            nx = x + dx
            ny = y + dy

            if not self.is_inside_map(nx, ny):
                continue

            if (nx, ny) in path:
                continue

            path.append((nx, ny))
            x, y = nx, ny

        return path

    def generate_board(self):
        self.path = self.generate_random_path()
        self.tile_map = {}

        for position in self.path:
            self.tile_map[position] = self.get_random_tile_type()

        chest_count = max(3, PATH_LENGTH // 25)
        possible_positions = self.path[8:]

        for _ in range(chest_count):
            pos = random.choice(possible_positions)
            self.tile_map[pos] = "Treasure Chest"

    # ========================================================
    # UTILITY
    # ========================================================

    def current_player(self):
        return self.players[self.current_player_index]

    def add_message(self, message):
        self.message_log.append(message)

        if len(self.message_log) > 8:
            self.message_log.pop(0)

    def roll_dice(self):
        return random.randint(MIN_DICE_ROLL, MAX_DICE_ROLL)

    def get_other_players(self, player):
        return [p for p in self.players if p is not player]

    def get_random_target(self, player):
        return random.choice(self.get_other_players(player))

    def damage_player(self, player, damage):
        if player.cactus_protected:
            player.cactus_protected = False
            self.add_message(f"{player.name}'s cactus blocked damage!")
            return

        player.hp -= damage
        self.add_message(f"{player.name} took {damage} damage.")

    def steal_keys(self, thief, victim, amount):
        stolen = min(victim.keys, amount)
        victim.keys -= stolen
        thief.keys += stolen
        self.add_message(f"{thief.name} stole {stolen} keys from {victim.name}.")

    def respawn_if_dead(self, player):
        if player.hp > 0:
            return

        self.add_message(f"{player.name} was knocked out!")

        player.hp = MAX_HP
        player.keys = max(0, player.keys // 2)
        player.path_index = random.randint(0, len(self.path) - 1)
        player.cactus_protected = False
        player.poison_turns = 0

        self.add_message(f"{player.name} respawned.")

    def check_winner(self):
        for player in self.players:
            if player.goblets >= GOBLETS_TO_WIN:
                self.game_over = True
                self.winner = player
                self.add_message(f"{player.name} wins!")
                return

    def next_turn(self):
        self.check_winner()

        if self.game_over:
            return

        self.current_player_index += 1

        if self.current_player_index >= len(self.players):
            self.current_player_index = 0
            self.round_number += 1

        self.selected_item_index = None

    # ========================================================
    # MOVEMENT AND TILE EFFECTS
    # ========================================================

    def move_player(self, player, spaces):
        old_index = player.path_index
        player.path_index = (player.path_index + spaces) % len(self.path)

        self.add_message(f"{player.name} moved {old_index} → {player.path_index}.")

    def apply_start_of_turn_status(self, player):
        if player.poison_turns > 0:
            self.add_message(f"{player.name} suffers nuclear waste.")
            self.damage_player(player, 4)
            player.poison_turns -= 1
            self.respawn_if_dead(player)

    def apply_tile_effect(self, player):
        position = self.path[player.path_index]
        tile_type = self.tile_map[position]

        self.add_message(f"{player.name} landed on {tile_type}.")

        if tile_type == "Key":
            gained = random.randint(3, 10)
            player.keys += gained
            self.add_message(f"{player.name} gained {gained} keys.")

        elif tile_type == "Healing":
            player.hp = MAX_HP
            self.add_message(f"{player.name} healed to full HP.")

        elif tile_type == "Item":
            item = random.choice(NORMAL_ITEMS)
            player.items.append(item)
            self.add_message(f"{player.name} got {item}.")

        elif tile_type == "Reaper":
            if random.choice(["keys", "hp"]) == "keys":
                lost = random.randint(5, 12)
                player.keys = max(0, player.keys - lost)
                self.add_message(f"Reaper stole {lost} keys.")
            else:
                self.damage_player(player, random.randint(5, 12))

        elif tile_type == "Weapon Cache":
            item = random.choice(POWERFUL_ITEMS)
            player.items.append(item)
            self.add_message(f"{player.name} found {item}.")

        elif tile_type == "White Skull":
            self.damage_player(player, random.randint(5, 12))
            lost = random.randint(2, 8)
            player.keys = max(0, player.keys - lost)
            self.add_message(f"{player.name} lost {lost} keys.")

        elif tile_type == "Event":
            self.random_event(player)

        elif tile_type == "Treasure Chest":
            if player.keys >= CHEST_COST:
                player.keys -= CHEST_COST
                player.goblets += 1
                self.add_message(f"{player.name} bought a goblet!")
            else:
                self.add_message(f"Need {CHEST_COST} keys for chest.")

        self.respawn_if_dead(player)
        self.check_winner()

    def random_event(self, player):
        event = random.choice([
            "key_rain",
            "damage_trap",
            "bonus_item",
            "lose_keys",
            "everyone_keys",
            "nothing",
        ])

        if event == "key_rain":
            gained = random.randint(5, 15)
            player.keys += gained
            self.add_message(f"Event: +{gained} keys!")

        elif event == "damage_trap":
            self.add_message("Event: trap!")
            self.damage_player(player, random.randint(4, 10))

        elif event == "bonus_item":
            item = random.choice(NORMAL_ITEMS)
            player.items.append(item)
            self.add_message(f"Event: gained {item}!")

        elif event == "lose_keys":
            lost = random.randint(5, 15)
            player.keys = max(0, player.keys - lost)
            self.add_message(f"Event: lost {lost} keys.")

        elif event == "everyone_keys":
            for p in self.players:
                p.keys += 5
            self.add_message("Event: everyone gained 5 keys!")

        else:
            self.add_message("Event: nothing happened.")

    # ========================================================
    # ITEMS
    # ========================================================

    def use_item(self, user, item):
        self.add_message(f"{user.name} used {item}!")

        if item == "Boxing Glove":
            target = self.get_random_target(user)
            self.damage_player(target, MAX_HP)

        elif item == "Shotgun":
            target = self.get_random_target(user)
            self.damage_player(target, random.randint(15, 17))

        elif item == "Health Kit":
            healed = random.randint(10, 20)
            user.hp = min(MAX_HP, user.hp + healed)
            self.add_message(f"{user.name} healed {healed} HP.")

        elif item == "Bee Hive":
            for target in self.get_other_players(user):
                distance = abs(target.path_index - user.path_index)
                wrapped_distance = len(self.path) - distance

                if min(distance, wrapped_distance) <= 2:
                    self.damage_player(target, random.randint(5, 9))

        elif item == "Magnet":
            target = self.get_random_target(user)
            self.steal_keys(user, target, random.randint(5, 15))

            if target.items:
                stolen_item = random.choice(target.items)
                target.items.remove(stolen_item)
                user.items.append(stolen_item)
                self.add_message(f"Also stole {stolen_item}.")

        elif item == "Eggplant":
            target = self.get_random_target(user)
            self.damage_player(target, random.randint(10, 16))

            lost = min(target.keys, random.randint(4, 10))
            target.keys -= lost
            self.add_message(f"{target.name} dropped {lost} keys.")

        elif item == "Swap Portal":
            target = self.get_random_target(user)
            user.path_index, target.path_index = target.path_index, user.path_index
            self.add_message(f"Swapped with {target.name}.")

        elif item == "Wrecking Ball":
            target = self.get_random_target(user)
            self.damage_player(target, random.randint(9, 10))

        elif item == "Rocket Skewer":
            old_index = user.path_index
            passed = []

            current = old_index
            for _ in range(10):
                current = (current + 1) % len(self.path)
                passed.append(current)

            for target in self.get_other_players(user):
                if target.path_index in passed:
                    self.damage_player(target, 15)

            user.path_index = passed[-1]
            self.add_message(f"{user.name} rocketed 10 spaces.")

        elif item == "Tactical Cactus Disguise":
            user.cactus_protected = True
            self.add_message(f"{user.name} is cactus protected.")

        elif item == "Present":
            outcome = random.choice([
                "teleport_to_chest",
                "gain_item",
                "lose_turn",
            ])

            if outcome == "teleport_to_chest":
                chest_indexes = [
                    i for i, pos in enumerate(self.path)
                    if self.tile_map[pos] == "Treasure Chest"
                ]

                if chest_indexes:
                    user.path_index = random.choice(chest_indexes)
                    self.add_message("Present teleported to chest!")

            elif outcome == "gain_item":
                new_item = random.choice(NORMAL_ITEMS)
                user.items.append(new_item)
                self.add_message(f"Present gave {new_item}.")

            else:
                user.skip_turns += 1
                self.add_message("Present trapped player.")

        elif item == "Dark Summoning Staff":
            target = self.get_random_target(user)
            self.damage_player(target, MAX_HP)
            self.add_message("Dark creature attacked!")

        elif item == "Giga Laser":
            target = self.get_random_target(user)
            self.damage_player(target, MAX_HP)
            self.add_message(f"Giga Laser hit {target.name}!")

        elif item == "Nuclear Waste Barrel":
            target = self.get_random_target(user)
            target.poison_turns = 3
            self.damage_player(target, random.randint(4, 7))
            self.add_message(f"{target.name} is poisoned.")

        elif item == "Arcade Challenge":
            target = self.get_random_target(user)

            user_score = random.randint(1, 20)
            target_score = random.randint(1, 20)

            if user_score >= target_score:
                user.goblets += 1
                self.add_message(f"{user.name} won Arcade Challenge!")
            else:
                target.goblets += 1
                self.add_message(f"{target.name} won Arcade Challenge!")

        for player in self.players:
            self.respawn_if_dead(player)

        self.check_winner()

    def human_use_selected_item(self):
        player = self.current_player()

        if player.is_ai or self.game_over:
            return

        if self.selected_item_index is None:
            self.add_message("Select an item first.")
            return

        if self.selected_item_index < 0 or self.selected_item_index >= len(player.items):
            return

        item = player.items.pop(self.selected_item_index)
        self.selected_item_index = None
        self.use_item(player, item)

    def ai_maybe_use_item(self, player):
        if not player.items:
            return

        if random.random() > 0.45:
            return

        item = random.choice(player.items)
        player.items.remove(item)
        self.use_item(player, item)

    # ========================================================
    # TURN LOGIC
    # ========================================================

    def human_roll(self):
        if self.game_over:
            return

        player = self.current_player()

        if player.is_ai:
            return

        if player.skip_turns > 0:
            player.skip_turns -= 1
            self.add_message("You skipped your turn.")
            self.next_turn()
            return

        self.apply_start_of_turn_status(player)

        if self.game_over:
            return

        roll = self.roll_dice()
        self.last_roll = roll
        self.add_message(f"You rolled {roll}.")

        self.move_player(player, roll)
        self.apply_tile_effect(player)

        self.next_turn()

    def ai_take_turn(self):
        if self.game_over:
            return

        player = self.current_player()

        if not player.is_ai:
            return

        if player.skip_turns > 0:
            player.skip_turns -= 1
            self.add_message(f"{player.name} skipped turn.")
            self.next_turn()
            return

        self.apply_start_of_turn_status(player)

        if self.game_over:
            return

        self.ai_maybe_use_item(player)

        if self.game_over:
            return

        roll = self.roll_dice()
        self.last_roll = roll
        self.add_message(f"{player.name} rolled {roll}.")

        self.move_player(player, roll)
        self.apply_tile_effect(player)

        self.next_turn()

    # ========================================================
    # DRAWING
    # ========================================================

    def board_to_screen(self, grid_x, grid_y):
        map_pixel_width = GRID_WIDTH * TILE_SIZE
        map_pixel_height = GRID_HEIGHT * TILE_SIZE

        offset_x = (BOARD_WIDTH - map_pixel_width) // 2
        offset_y = (SCREEN_HEIGHT - map_pixel_height) // 2

        screen_x = offset_x + grid_x * TILE_SIZE
        screen_y = offset_y + (GRID_HEIGHT - 1 - grid_y) * TILE_SIZE

        return screen_x, screen_y

    def draw_board(self):
        pygame.draw.rect(self.screen, BACKGROUND, (0, 0, BOARD_WIDTH, SCREEN_HEIGHT))

        # Draw faint grid.
        for x in range(GRID_WIDTH):
            for y in range(GRID_HEIGHT):
                sx, sy = self.board_to_screen(x, y)
                rect = pygame.Rect(sx + TILE_GAP, sy + TILE_GAP, TILE_SIZE - TILE_GAP, TILE_SIZE - TILE_GAP)
                pygame.draw.rect(self.screen, (28, 31, 42), rect, border_radius=6)

        # Draw path lines.
        for i in range(len(self.path) - 1):
            x1, y1 = self.path[i]
            x2, y2 = self.path[i + 1]

            sx1, sy1 = self.board_to_screen(x1, y1)
            sx2, sy2 = self.board_to_screen(x2, y2)

            p1 = (sx1 + TILE_SIZE // 2, sy1 + TILE_SIZE // 2)
            p2 = (sx2 + TILE_SIZE // 2, sy2 + TILE_SIZE // 2)

            pygame.draw.line(self.screen, (75, 80, 100), p1, p2, 7)

        # Draw tiles.
        for index, position in enumerate(self.path):
            x, y = position
            tile_type = self.tile_map[position]
            color = TILE_COLORS[tile_type]

            sx, sy = self.board_to_screen(x, y)
            rect = pygame.Rect(sx + TILE_GAP, sy + TILE_GAP, TILE_SIZE - TILE_GAP, TILE_SIZE - TILE_GAP)

            pygame.draw.rect(self.screen, color, rect, border_radius=8)
            pygame.draw.rect(self.screen, (15, 15, 20), rect, 2, border_radius=8)

            letter = TILE_TYPES[tile_type]
            label = self.font_small.render(letter, True, BLACK)
            label_rect = label.get_rect(center=rect.center)
            self.screen.blit(label, label_rect)

            if index == 0:
                start_label = self.font_small.render("START", True, WHITE)
                self.screen.blit(start_label, (sx - 4, sy - 18))

        # Draw players.
        player_offsets = [
            (-8, -8),
            (8, -8),
            (-8, 8),
            (8, 8),
        ]

        for i, player in enumerate(self.players):
            grid_x, grid_y = self.path[player.path_index]
            sx, sy = self.board_to_screen(grid_x, grid_y)

            ox, oy = player_offsets[i]
            center = (
                sx + TILE_SIZE // 2 + ox,
                sy + TILE_SIZE // 2 + oy,
            )

            pygame.draw.circle(self.screen, PLAYER_COLORS[player.symbol], center, 9)
            pygame.draw.circle(self.screen, BLACK, center, 9, 2)

            label = self.font_small.render(player.symbol, True, BLACK)
            label_rect = label.get_rect(center=center)
            self.screen.blit(label, label_rect)

    def draw_side_panel(self):
        panel_rect = pygame.Rect(BOARD_WIDTH, 0, SIDE_PANEL_WIDTH, SCREEN_HEIGHT)
        pygame.draw.rect(self.screen, PANEL_BG, panel_rect)

        x = BOARD_WIDTH + 24
        y = 20

        title = self.font_title.render("Board Game", True, TEXT)
        self.screen.blit(title, (x, y))
        y += 52

        round_text = self.font.render(f"Round: {self.round_number}", True, TEXT)
        self.screen.blit(round_text, (x, y))
        y += 30

        current = self.current_player()
        turn_text = self.font.render(f"Turn: {current.name}", True, TEXT)
        self.screen.blit(turn_text, (x, y))
        y += 30

        if self.last_roll is not None:
            dice_text = self.font.render(f"Last Roll: {self.last_roll}", True, TEXT)
            self.screen.blit(dice_text, (x, y))
        y += 42

        goal_text = self.font_small.render(f"Goal: {GOBLETS_TO_WIN} goblets | Chest: {CHEST_COST} keys", True, MUTED_TEXT)
        self.screen.blit(goal_text, (x, y))
        y += 36

        # Player cards.
        for player in self.players:
            card = pygame.Rect(x, y, SIDE_PANEL_WIDTH - 48, 82)
            pygame.draw.rect(self.screen, PANEL_DARK, card, border_radius=12)

            pygame.draw.circle(self.screen, PLAYER_COLORS[player.symbol], (x + 22, y + 24), 11)
            symbol = self.font_small.render(player.symbol, True, BLACK)
            self.screen.blit(symbol, symbol.get_rect(center=(x + 22, y + 24)))

            name = self.font.render(player.name, True, TEXT)
            self.screen.blit(name, (x + 42, y + 10))

            stats = self.font_small.render(
                f"HP {player.hp}/{MAX_HP}   Keys {player.keys}   Goblets {player.goblets}",
                True,
                MUTED_TEXT
            )
            self.screen.blit(stats, (x + 16, y + 38))

            status = self.font_small.render(f"Status: {player.status_text()}", True, MUTED_TEXT)
            self.screen.blit(status, (x + 16, y + 58))

            y += 92

        # Human items.
        y += 5
        item_title = self.font.render("Your Items", True, TEXT)
        self.screen.blit(item_title, (x, y))
        y += 28

        human = self.players[0]

        if not human.items:
            none_text = self.font_small.render("No items yet.", True, MUTED_TEXT)
            self.screen.blit(none_text, (x, y))
            y += 24
        else:
            for i, item in enumerate(human.items[:5]):
                item_rect = pygame.Rect(x, y, SIDE_PANEL_WIDTH - 48, 25)

                if self.selected_item_index == i:
                    pygame.draw.rect(self.screen, (90, 110, 170), item_rect, border_radius=7)
                else:
                    pygame.draw.rect(self.screen, (46, 50, 66), item_rect, border_radius=7)

                item_label = self.font_small.render(f"{i + 1}. {item}", True, TEXT)
                self.screen.blit(item_label, (x + 8, y + 4))
                y += 29

            if len(human.items) > 5:
                more = self.font_small.render(f"+{len(human.items) - 5} more", True, MUTED_TEXT)
                self.screen.blit(more, (x, y))
                y += 20

        # Message log.
        y = 455
        log_title = self.font.render("Log", True, TEXT)
        self.screen.blit(log_title, (x, y))
        y += 30

        log_box = pygame.Rect(x, y, SIDE_PANEL_WIDTH - 48, 120)
        pygame.draw.rect(self.screen, PANEL_DARK, log_box, border_radius=12)

        log_y = y + 8
        for message in self.message_log[-6:]:
            msg = self.font_small.render(message[:38], True, MUTED_TEXT)
            self.screen.blit(msg, (x + 10, log_y))
            log_y += 18

        # Buttons.
        mouse_pos = pygame.mouse.get_pos()

        if not self.current_player().is_ai and not self.game_over:
            self.roll_button.draw(self.screen, mouse_pos)
            self.use_item_button.draw(self.screen, mouse_pos)

        self.new_game_button.draw(self.screen, mouse_pos)

        if self.game_over:
            overlay = pygame.Rect(BOARD_WIDTH + 25, 580, SIDE_PANEL_WIDTH - 50, 60)
            pygame.draw.rect(self.screen, (95, 65, 140), overlay, border_radius=14)

            winner_text = self.font_big.render(f"{self.winner.name} wins!", True, WHITE)
            self.screen.blit(winner_text, winner_text.get_rect(center=overlay.center))

    def draw_tile_legend(self):
        x = 20
        y = 20

        legend_bg = pygame.Rect(x - 10, y - 10, 170, 260)
        pygame.draw.rect(self.screen, (30, 33, 44), legend_bg, border_radius=12)

        title = self.font.render("Tiles", True, TEXT)
        self.screen.blit(title, (x, y))
        y += 34

        for tile_name, symbol in TILE_TYPES.items():
            color = TILE_COLORS[tile_name]
            pygame.draw.rect(self.screen, color, (x, y + 2, 18, 18), border_radius=4)
            label = self.font_small.render(f"{symbol} = {tile_name}", True, TEXT)
            self.screen.blit(label, (x + 28, y + 2))
            y += 26

    def draw(self):
        self.screen.fill(BACKGROUND)
        self.draw_board()
        self.draw_tile_legend()
        self.draw_side_panel()
        pygame.display.flip()

    # ========================================================
    # INPUT
    # ========================================================

    def handle_item_click(self, mouse_pos):
        human = self.players[0]

        x = BOARD_WIDTH + 24
        y = 20 + 52 + 30 + 30 + 42 + 36
        y += 92 * len(self.players)
        y += 5 + 28

        for i, item in enumerate(human.items[:5]):
            item_rect = pygame.Rect(x, y, SIDE_PANEL_WIDTH - 48, 25)

            if item_rect.collidepoint(mouse_pos):
                self.selected_item_index = i
                self.add_message(f"Selected {item}.")
                return

            y += 29

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self.handle_item_click(event.pos)

                if self.roll_button.clicked(event):
                    self.roll_button.action()

                if self.use_item_button.clicked(event):
                    self.use_item_button.action()

                if self.new_game_button.clicked(event):
                    self.new_game_button.action()

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE:
                    if not self.current_player().is_ai:
                        self.human_roll()

                if pygame.K_1 <= event.key <= pygame.K_5:
                    index = event.key - pygame.K_1
                    if index < len(self.players[0].items):
                        self.selected_item_index = index
                        self.add_message(f"Selected {self.players[0].items[index]}.")

                if event.key == pygame.K_e:
                    self.human_use_selected_item()

                if event.key == pygame.K_r:
                    self.reset_game()

    # ========================================================
    # MAIN LOOP
    # ========================================================

    def run(self):
        ai_timer = 0

        while True:
            dt = self.clock.tick(FPS)
            self.handle_events()

            if not self.game_over and self.current_player().is_ai:
                ai_timer += dt

                if ai_timer > 900:
                    self.ai_take_turn()
                    ai_timer = 0
            else:
                ai_timer = 0

            self.draw()


if __name__ == "__main__":
    Game().run()