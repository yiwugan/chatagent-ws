import pygame
import random

# Initialize pygame
pygame.init()

# Screen dimensions
WIDTH, HEIGHT = 800, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Animated Landscape")

# Colors
BLUE = (135, 206, 235)
GREEN = (34, 139, 34)
BROWN = (139, 69, 19)
WHITE = (255, 255, 255)

# Sun properties
sun_x, sun_y = 100, 400
sun_direction = 1  # 1 for rising, -1 for setting
sun_color = (255, 204, 0)  # Yellow-orange

# Cloud properties
clouds = [[random.randint(600, 800), random.randint(50, 150)] for _ in range(3)]

# Tree properties
tree_x, tree_y = 600, 400
tree_size = 1.0
size_direction = 1  # 1 for growing, -1 for shrinking

clock = pygame.time.Clock()
frames = 0  # Frame counter

running = True
while running:
    screen.fill(BLUE)  # Sky background
    frames += 1  # Increment frame count

    # Sun Animation
    if sun_y <= 100:
        sun_direction = -1  # Start setting
    elif sun_y >= 400:
        sun_direction = 1  # Start rising
    sun_y -= sun_direction  # Move sun up/down
    sun_color = (255, 204 - (sun_y // 4), 0)  # Change sun color gradually
    pygame.draw.circle(screen, sun_color, (sun_x, sun_y), 50)

    # Ground
    pygame.draw.rect(screen, GREEN, (0, HEIGHT - 200, WIDTH, 200))

    # Cloud Animation
    for cloud in clouds:
        cloud[0] -= 1  # Move left
        if cloud[0] < -50:
            cloud[0] = WIDTH + 50  # Reset cloud position
        pygame.draw.circle(screen, WHITE, cloud, 30)
        pygame.draw.circle(screen, WHITE, (cloud[0] + 30, cloud[1] + 10), 25)
        pygame.draw.circle(screen, WHITE, (cloud[0] - 30, cloud[1] + 10), 25)

    # Tree Animation
    if frames % 50 == 0:  # Change size every 50 frames
        if tree_size >= 1.2:
            size_direction = -1  # Shrinking
        elif tree_size <= 0.8:
            size_direction = 1  # Growing
        tree_size += size_direction * 0.05
    tree_trunk = pygame.Rect(tree_x - 10, tree_y, 20, 60)
    pygame.draw.rect(screen, BROWN, tree_trunk)
    pygame.draw.circle(screen, GREEN, (tree_x, tree_y - 20), int(30 * tree_size))
    pygame.draw.circle(screen, GREEN, (tree_x - 20, tree_y - 10), int(25 * tree_size))
    pygame.draw.circle(screen, GREEN, (tree_x + 20, tree_y - 10), int(25 * tree_size))

    # Event handling
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    pygame.display.flip()
    clock.tick(30)

pygame.quit()
