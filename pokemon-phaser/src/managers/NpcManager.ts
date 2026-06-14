import { Scene } from "phaser";
import { TILE_SIZE } from "../constants";
import { SpriteManager } from "./SpriteManager";
import { getSpriteUrl } from "../api";

// Define direction frame indices
enum DirectionFrame {
  DOWN = 0, // 0th frame for facing down
  UP = 1, // 1st frame for facing up
  LEFT = 2, // 2nd frame for facing left
  RIGHT = 2, // Same as LEFT but will be flipped
  WALKING_DOWN = 3, // 3rd frame for walking down
  WALKING_UP = 4, // 4th frame for walking up
  WALKING_LEFT = 5, // 5th frame for walking left
  WALKING_RIGHT = 5, // Same as WALKING_LEFT but will be flipped
}

export class NpcManager {
  private scene: Scene;
  private npcSpriteCache: Map<string, string> = new Map();

  constructor(scene: Scene, _spriteManager: SpriteManager) {
    this.scene = scene;
  }

  /**
   * Get the frame index for an NPC based on its action_type and action_direction
   * @param actionType The NPC's action type (e.g., "STAY", "WALK")
   * @param actionDirection The NPC's action direction (e.g., "DOWN", "UP", "LEFT", "RIGHT")
   * @returns An object with the frame index and whether to flip the sprite horizontally
   */
  getFrameForDirection(
    actionType: string,
    actionDirection: string,
    _spriteKey?: string
  ): { frame: number; flipX: boolean } {
    // Default to facing down
    let frame = DirectionFrame.DOWN;
    let flipX = false;

    if (!actionDirection) {
      return { frame, flipX };
    }

    // Convert to uppercase for consistency
    const direction = actionDirection.toUpperCase();
    const type = (actionType || "STAY").toUpperCase();

    // Handle different action types
    const isWalking = type === "WALK";

    // Some exported actor rows can carry an explicit frame and flip state.
    if (
      (actionType as any)?.frame !== undefined &&
      (actionType as any)?.flipX !== undefined
    ) {
      return {
        frame: (actionType as any).frame,
        flipX: (actionType as any).flipX,
      };
    }

    // Determine the frame based on direction
    switch (direction) {
      case "DOWN":
        frame = isWalking ? DirectionFrame.WALKING_DOWN : DirectionFrame.DOWN;
        break;
      case "UP":
        frame = isWalking ? DirectionFrame.WALKING_UP : DirectionFrame.UP;
        break;
      case "LEFT":
        frame = isWalking ? DirectionFrame.WALKING_LEFT : DirectionFrame.LEFT;
        flipX = false;
        break;
      case "RIGHT":
        frame = isWalking ? DirectionFrame.WALKING_RIGHT : DirectionFrame.RIGHT;
        flipX = true;
        break;
      case "UP_DOWN":
      case "LEFT_RIGHT":
      case "ANY_DIR":
        // For movement patterns, default to DOWN when not moving
        frame = DirectionFrame.DOWN;
        break;
      default:
        console.warn(`Unknown direction: ${direction}, defaulting to DOWN`);
        frame = DirectionFrame.DOWN;
    }

    return { frame, flipX };
  }

  async loadNpcSprite(spriteName: string): Promise<string> {
    if (!spriteName) {
      return "npc-fallback";
    }

    // Convert sprite name format (e.g., SPRITE_BRUNETTE_GIRL -> brunette_girl.png)
    // Remove SPRITE_ prefix, convert to lowercase, and add .png extension
    const spriteFileName =
      spriteName.replace("SPRITE_", "").toLowerCase() + ".png";
    const spriteKey = `npc-${spriteFileName.replace(".png", "")}`;

    // Check if we've already loaded this sprite
    if (this.scene.textures.exists(spriteKey)) {
      console.log(`NPC Sprite ${spriteKey} already loaded, reusing`);
      const frameCount = this.scene.textures.get(spriteKey).frameTotal;
      console.log(`Sprite ${spriteKey} has ${frameCount} frames`);
      return spriteKey;
    }

    try {
      // Log the sprite loading attempt
      console.log(`Loading NPC sprite: ${spriteName} -> ${spriteFileName}`);

      // Store in cache for future reference
      this.npcSpriteCache.set(spriteName, spriteKey);

      // Load the sprite as a spritesheet with 6 frames (0-5)
      // Frame 0: Down facing
      // Frame 1: Up facing
      // Frame 2: Left facing
      // Frame 3: Walking down
      // Frame 4: Walking up
      // Frame 5: Walking left
      this.scene.load.spritesheet(spriteKey, getSpriteUrl(spriteFileName), {
        frameWidth: TILE_SIZE,
        frameHeight: TILE_SIZE,
      });

      // Add error handler for this specific sprite
      this.scene.load.once(`filecomplete-spritesheet-${spriteKey}`, () => {
        console.log(`Successfully loaded sprite: ${spriteKey}`);
        const frameCount = this.scene.textures.get(spriteKey).frameTotal;
        console.log(`Sprite ${spriteKey} has ${frameCount} frames`);
      });

      this.scene.load.once(`loaderror`, (fileObj: any) => {
        if (fileObj.key === spriteKey) {
          console.error(
            `Failed to load sprite: ${spriteKey}, URL: ${getSpriteUrl(
              spriteFileName
            )}`
          );
        }
      });

      // Start the load
      await new Promise<void>((resolve) => {
        this.scene.load.once("complete", () => {
          resolve();
        });
        this.scene.load.start();
      });

      // Verify the texture was loaded
      if (this.scene.textures.exists(spriteKey)) {
        console.log(`Verified NPC sprite ${spriteKey} is loaded`);
        const frameCount = this.scene.textures.get(spriteKey).frameTotal;
        console.log(`Sprite ${spriteKey} has ${frameCount} frames`);
        return spriteKey;
      } else {
        console.warn(`NPC sprite ${spriteKey} failed to load despite no error`);
        return "npc-fallback";
      }
    } catch (error) {
      console.error(`Error loading NPC sprite ${spriteName}:`, error);
      return "npc-fallback";
    }
  }

  async preloadNpcSprites(npcs: any[]) {
    console.log(`Preloading ${npcs.length} NPC sprites`);

    // Create a set of unique sprite names to load
    const spriteNames = new Set<string>();

    for (const npc of npcs) {
      if (npc.sprite_name) {
        spriteNames.add(npc.sprite_name);
      }
    }

    console.log(`Found ${spriteNames.size} unique sprite names to load`);

    // Load each sprite
    const promises = Array.from(spriteNames).map((spriteName) =>
      this.loadNpcSprite(spriteName)
    );

    // Wait for all sprites to load
    await Promise.all(promises);

    console.log(`Finished preloading NPC sprites`);
  }

  clearCache() {
    this.npcSpriteCache.clear();
  }
}
