import { Scene } from "phaser";
import { TILE_SIZE } from "../constants";

export class MapRenderer {
  private scene: Scene;
  private mapContainer: Phaser.GameObjects.Container;
  private tileImages: Map<number, Phaser.GameObjects.Image> = new Map();
  private npcSprites: Map<number, Phaser.GameObjects.Sprite> = new Map();

  constructor(scene: Scene, mapContainer: Phaser.GameObjects.Container) {
    this.scene = scene;
    this.mapContainer = mapContainer;
  }

  renderMap(tiles: any[], items: any[], warps: any[] = [], npcs: any[] = []) {
    console.log(
      `Rendering map with ${tiles.length} tiles, ${items.length} items, ${warps.length} warps, ${npcs.length} NPCs`
    );

    // Clear existing map - ensure proper cleanup
    this.clear();

    // Render each tile
    for (const tile of tiles) {
      const { x, y, tile_image_id, map_id } = tile;

      // Calculate position
      const posX = x * TILE_SIZE;
      const posY = y * TILE_SIZE;

      // Create tile sprite using the texture (either preloaded or fallback)
      const tileKey = `tile-${tile_image_id}`;

      // Create the tile sprite normally
      const tileSprite = this.scene.add.image(posX, posY, tileKey);
      tileSprite.setOrigin(0, 0);
      tileSprite.setDisplaySize(TILE_SIZE, TILE_SIZE);

      // Store the tile_image_id and map_id for later reference
      (tileSprite as any).tileImageId = tile_image_id;
      (tileSprite as any).mapId = map_id;

      // Add to container
      this.mapContainer.add(tileSprite);

      // Store reference
      this.tileImages.set(x * 1000 + y, tileSprite);
    }

    // Render items
    for (const item of items) {
      // Use the item-marker texture (poke_ball.png)
      const textureKey = this.scene.textures.exists("item-marker")
        ? "item-marker"
        : "item-marker-fallback";

      const itemSprite = this.scene.add.image(
        item.x * TILE_SIZE + TILE_SIZE / 2,
        item.y * TILE_SIZE + TILE_SIZE / 2,
        textureKey
      );

      // Set appropriate size based on the texture used
      if (textureKey === "item-marker-fallback") {
        itemSprite.setDisplaySize(TILE_SIZE / 2, TILE_SIZE / 2);
      } else {
        // Set an appropriate size for the poke_ball image
        itemSprite.setDisplaySize(TILE_SIZE * 0.75, TILE_SIZE * 0.75);
      }

      // Store item data in the sprite for hover info
      (itemSprite as any).itemData = item;

      // Add to container
      this.mapContainer.add(itemSprite);
    }

    // Render warps
    for (const warp of warps) {
      // Create a transparent red square for each warp
      const warpGraphics = this.scene.add.rectangle(
        warp.x * TILE_SIZE + TILE_SIZE / 2,
        warp.y * TILE_SIZE + TILE_SIZE / 2,
        TILE_SIZE,
        TILE_SIZE,
        0xff0000,
        0.5
      );

      // Store warp data in the graphics object for hover info
      (warpGraphics as any).warpData = warp;

      // Make the warp interactive
      warpGraphics.setInteractive();

      // Add a pointer cursor on hover
      warpGraphics.on("pointerover", () => {
        this.scene.input.setDefaultCursor("pointer");
      });

      warpGraphics.on("pointerout", () => {
        this.scene.input.setDefaultCursor("default");
      });

      // Emit an event when the warp is clicked
      warpGraphics.on("pointerdown", () => {
        // Add a visual effect when clicking
        this.scene.tweens.add({
          targets: warpGraphics,
          alpha: { from: 0.5, to: 1 },
          duration: 150,
          yoyo: true,
          repeat: 2,
          onComplete: () => {
            // Emit the warp clicked event after the effect completes
            this.scene.events.emit("warpClicked", warp);
          },
        });
      });

      // Add to container
      this.mapContainer.add(warpGraphics);
    }

    // Render NPCs
    for (const npc of npcs) {
      // Log NPC data for debugging
      console.log(
        `Rendering NPC: ${npc.name || "Unnamed"}, sprite: ${
          npc.sprite_name || "none"
        }, position: (${npc.x}, ${npc.y}), action_type: ${
          npc.action_type || "STAY"
        }, action_direction: ${npc.action_direction || "DOWN"}`
      );

      // Calculate position (center of the tile)
      const posX = npc.x * TILE_SIZE + TILE_SIZE / 2;
      const posY = npc.y * TILE_SIZE + TILE_SIZE / 2;

      // Create the NPC sprite
      let npcSprite: Phaser.GameObjects.Sprite;

      // Try to get the TileManager from the scene
      const tileManager = (this.scene as any).tileManager;

      if (!tileManager) {
        console.error("TileManager not found in scene, using fallback sprite");
        // Create a fallback sprite if TileManager is not available
        npcSprite = this.createFallbackNpcSprite(posX, posY);
      } else {
        // Create a temporary fallback sprite while we load the real one
        npcSprite = this.scene.add.sprite(posX, posY, "npc-fallback");

        // Load the sprite asynchronously
        this.loadNpcSpriteAsync(npc, npcSprite, tileManager);
      }

      // Store NPC data in the sprite for hover info
      (npcSprite as any).npcData = npc;

      // Add to container
      this.mapContainer.add(npcSprite);

      // Store reference
      this.npcSprites.set(npc.id, npcSprite);
    }

    return this.calculateMapBounds(tiles);
  }

  private createFallbackNpcSprite(
    x: number,
    y: number
  ): Phaser.GameObjects.Sprite {
    // Create a fallback sprite if it doesn't exist
    if (!this.scene.textures.exists("npc-fallback")) {
      const graphics = this.scene.make.graphics({ x: 0, y: 0 });
      graphics.fillStyle(0x0000ff); // Blue color for fallback NPC
      graphics.fillRect(0, 0, TILE_SIZE, TILE_SIZE);
      graphics.lineStyle(1, 0xffffff);
      graphics.strokeRect(0, 0, TILE_SIZE, TILE_SIZE);
      graphics.generateTexture("npc-fallback", TILE_SIZE, TILE_SIZE);
      graphics.destroy();
    }

    return this.scene.add.sprite(x, y, "npc-fallback");
  }

  private async loadNpcSpriteAsync(
    npc: any,
    npcSprite: Phaser.GameObjects.Sprite,
    tileManager: any
  ) {
    try {
      // Load the sprite
      const spriteKey = await tileManager.loadNpcSprite(npc.sprite_name);

      // Update the sprite texture if it still exists and is active
      if (npcSprite && npcSprite.active) {
        console.log(`Setting NPC sprite texture to ${spriteKey}`);

        // Get the frame and flip state
        let frame = 0;
        let flipX = false;

        // Exported actor data may include an explicit frame and flip state.
        if (npc.frame !== undefined && npc.flipX !== undefined) {
          frame = npc.frame;
          flipX = npc.flipX;
          console.log(`Using exported frame: ${frame}, flipX: ${flipX}`);
        } else {
          // Otherwise, get them from the NPC manager
          const npcManager = tileManager.getNpcManager();
          if (npcManager) {
            // Get the frame and flip state based on action_type and action_direction
            const frameInfo = npcManager.getFrameForDirection(
              npc.action_type || "STAY",
              npc.action_direction || "DOWN",
              spriteKey
            );
            frame = frameInfo.frame;
            flipX = frameInfo.flipX;

            console.log(
              `NPC ${npc.name} action: ${
                npc.action_type || "STAY"
              }, direction: ${
                npc.action_direction || "DOWN"
              }, frame: ${frame}, flipX: ${flipX}`
            );
          }
        }

        // Set the texture with the correct frame
        npcSprite.setTexture(spriteKey, frame);

        // Set the horizontal flip
        npcSprite.setFlipX(flipX);

        // Set origin to center for proper positioning
        npcSprite.setOrigin(0.5, 0.5);
      } else {
        console.warn(`NPC sprite no longer active, can't update texture`);
      }
    } catch (error) {
      console.error(`Error loading NPC sprite for ${npc.name}:`, error);
    }
  }

  updateTile(x: number, y: number, newTileImageId: number): boolean {
    // Get the tile sprite at the specified position
    const tileSprite = this.tileImages.get(x * 1000 + y);

    if (!tileSprite) {
      console.warn(`No tile found at position (${x}, ${y})`);
      return false;
    }

    // Get the new texture key
    const newTileKey = `tile-${newTileImageId}`;

    // Check if the texture exists
    if (!this.scene.textures.exists(newTileKey)) {
      console.warn(`Texture ${newTileKey} does not exist`);
      return false;
    }

    // Update the texture
    tileSprite.setTexture(newTileKey);

    // Update the stored tile image ID
    (tileSprite as any).tileImageId = newTileImageId;

    return true;
  }

  updateNpcPosition(
    npcId: number,
    oldX: number,
    oldY: number,
    newX: number,
    newY: number
  ): boolean {
    // Get the NPC sprite
    const npcSprite = this.npcSprites.get(npcId);

    if (!npcSprite) {
      console.warn(`No NPC sprite found with ID ${npcId}`);
      return false;
    }

    // Calculate new position
    const posX = newX * TILE_SIZE + TILE_SIZE / 2;
    const posY = newY * TILE_SIZE + TILE_SIZE / 2;

    // Determine the direction of movement
    let direction = "DOWN";
    if (newY < oldY) direction = "UP";
    else if (newY > oldY) direction = "DOWN";
    else if (newX < oldX) direction = "LEFT";
    else if (newX > oldX) direction = "RIGHT";

    // Update the NPC data with the new direction
    const npcData = (npcSprite as any).npcData;
    if (npcData) {
      // Store the previous direction to restore after movement
      const previousDirection = npcData.action_direction;

      // Update the direction for the movement animation
      npcData.action_direction = direction;

      // Create a movement tween for smooth animation
      this.scene.tweens.add({
        targets: npcSprite,
        x: posX,
        y: posY,
        duration: 500, // Half a second for the movement
        ease: "Linear",
        onComplete: () => {
          // Update the NPC data in the sprite
          if (npcSprite.active) {
            npcData.x = newX;
            npcData.y = newY;

            // If the NPC has a specific movement pattern (UP_DOWN, LEFT_RIGHT, ANY_DIR),
            // restore the original action_direction after movement
            if (
              npcData.action_direction === "UP_DOWN" ||
              npcData.action_direction === "LEFT_RIGHT" ||
              npcData.action_direction === "ANY_DIR"
            ) {
              npcData.action_direction = previousDirection;
            }
          }
        },
      });
    } else {
      // If we don't have NPC data, just move the sprite without animation
      this.scene.tweens.add({
        targets: npcSprite,
        x: posX,
        y: posY,
        duration: 500,
        ease: "Linear",
      });
    }

    return true;
  }

  updateNpcAnimation(npcId: number, frame?: number, flipX?: boolean): boolean {
    // Get the NPC sprite
    const npcSprite = this.npcSprites.get(npcId);

    if (!npcSprite) {
      console.warn(`No NPC sprite found with ID ${npcId} for animation update`);
      return false;
    }

    // Update the sprite's frame if provided
    if (frame !== undefined) {
      npcSprite.setFrame(frame);

      // Also update the frame in the NPC data
      const npcData = (npcSprite as any).npcData;
      if (npcData) {
        npcData.frame = frame;
      }
    }

    // Update the sprite's flip state if provided
    if (flipX !== undefined) {
      npcSprite.setFlipX(flipX);

      // Also update the flipX in the NPC data
      const npcData = (npcSprite as any).npcData;
      if (npcData) {
        npcData.flipX = flipX;
      }
    }

    return true;
  }

  renderNpc(npc: any): boolean {
    // Check if this NPC is already rendered
    if (this.npcSprites.has(npc.id)) {
      console.warn(`NPC with ID ${npc.id} is already rendered`);
      return false;
    }

    // Calculate position (center of the tile)
    const posX = npc.x * TILE_SIZE + TILE_SIZE / 2;
    const posY = npc.y * TILE_SIZE + TILE_SIZE / 2;

    // Create the NPC sprite
    let npcSprite: Phaser.GameObjects.Sprite;

    // Try to get the TileManager from the scene
    const tileManager = (this.scene as any).tileManager;

    if (!tileManager) {
      console.error("TileManager not found in scene, using fallback sprite");
      // Create a fallback sprite if TileManager is not available
      npcSprite = this.createFallbackNpcSprite(posX, posY);
    } else {
      // Create a temporary fallback sprite while we load the real one
      npcSprite = this.scene.add.sprite(posX, posY, "npc-fallback");

      // If the exported actor includes frame data, apply it immediately.
      if (npc.frame !== undefined) {
        npcSprite.setFrame(npc.frame);
      }

      if (npc.flipX !== undefined) {
        npcSprite.setFlipX(npc.flipX);
      }

      // Load the sprite asynchronously
      this.loadNpcSpriteAsync(npc, npcSprite, tileManager);
    }

    // Store NPC data in the sprite for hover info
    (npcSprite as any).npcData = npc;

    // Add to container
    this.mapContainer.add(npcSprite);

    // Store reference
    this.npcSprites.set(npc.id, npcSprite);

    return true;
  }

  calculateMapBounds(tiles: any[]) {
    if (tiles.length === 0) {
      return { minX: 0, minY: 0, maxX: 0, maxY: 0, width: 0, height: 0 };
    }

    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;

    // Find the bounds of the map
    for (const tile of tiles) {
      minX = Math.min(minX, tile.x);
      minY = Math.min(minY, tile.y);
      maxX = Math.max(maxX, tile.x);
      maxY = Math.max(maxY, tile.y);
    }

    const mapWidth = (maxX - minX + 1) * TILE_SIZE;
    const mapHeight = (maxY - minY + 1) * TILE_SIZE;

    return {
      minX,
      minY,
      maxX,
      maxY,
      width: mapWidth,
      height: mapHeight,
      centerX: minX * TILE_SIZE + mapWidth / 2,
      centerY: minY * TILE_SIZE + mapHeight / 2,
    };
  }

  getTileAt(x: number, y: number) {
    return this.tileImages.get(x * 1000 + y);
  }

  clear() {
    console.log("Clearing map renderer");

    try {
      // Destroy all children to ensure proper cleanup
      if (this.mapContainer) {
        this.mapContainer.each((child: Phaser.GameObjects.GameObject) => {
          if (child) {
            child.destroy();
          }
        });

        // Remove all children from the container
        this.mapContainer.removeAll(true);
      }

      // Clear the tile images map
      this.tileImages.clear();

      // Clear the NPC sprites map
      this.npcSprites.clear();
    } catch (error) {
      console.error("Error clearing map renderer:", error);
    }
  }
}
