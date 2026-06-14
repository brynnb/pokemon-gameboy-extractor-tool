import { Scene } from "phaser";
import { MIN_ZOOM, MAX_ZOOM, DEFAULT_ZOOM, ZOOM_STEP } from "../constants";

// Define a constant for non-overworld zoom level
const NON_OVERWORLD_ZOOM = 2.0; // Much more zoomed in than DEFAULT_ZOOM

export class CameraController {
  private scene: Scene;
  private mainCamera: Phaser.Cameras.Scene2D.Camera;
  private uiCamera!: Phaser.Cameras.Scene2D.Camera; // UI camera for HUD elements
  private zoomLevel: number = DEFAULT_ZOOM;
  private isOverworld: boolean = true; // Track whether we're in overworld mode
  private cameraControls = {
    isDragging: false,
    lastPointerPosition: { x: 0, y: 0 },
  };

  // Store overworld camera state
  private overworldCameraState = {
    x: 0,
    y: 0,
    zoom: DEFAULT_ZOOM,
    saved: false,
  };

  constructor(scene: Scene) {
    this.scene = scene;
    this.mainCamera = scene.cameras.main;
    this.mainCamera.setBackgroundColor(0x000000);
    this.setupUiCamera();
    this.setupControls();
  }

  setupUiCamera() {
    // Create a separate camera for UI elements that won't be affected by zoom
    this.uiCamera = this.scene.cameras.add(
      0,
      0,
      this.mainCamera.width,
      this.mainCamera.height
    );
    this.uiCamera.setScroll(0, 0);
    this.uiCamera.transparent = true;
    this.uiCamera.setName("UICamera");
  }

  setupControls() {
    // Set up mouse/touch input for dragging
    this.scene.input.on("pointerdown", (pointer: Phaser.Input.Pointer) => {
      this.cameraControls.isDragging = true;
      this.cameraControls.lastPointerPosition = { x: pointer.x, y: pointer.y };
    });

    this.scene.input.on("pointerup", () => {
      this.cameraControls.isDragging = false;
    });

    this.scene.input.on("pointermove", (pointer: Phaser.Input.Pointer) => {
      if (this.cameraControls.isDragging) {
        const deltaX = pointer.x - this.cameraControls.lastPointerPosition.x;
        const deltaY = pointer.y - this.cameraControls.lastPointerPosition.y;

        // Adjust deltas based on zoom level to maintain consistent panning speed
        this.mainCamera.scrollX -= deltaX / this.zoomLevel;
        this.mainCamera.scrollY -= deltaY / this.zoomLevel;

        this.cameraControls.lastPointerPosition = {
          x: pointer.x,
          y: pointer.y,
        };
      }
    });

    // Set up mouse wheel for zooming
    this.scene.input.on(
      "wheel",
      (_pointer: any, _gameObjects: any, _deltaX: number, deltaY: number) => {
        // Zoom in/out based on wheel direction
        if (deltaY > 0) {
          this.zoomLevel = Math.max(MIN_ZOOM, this.zoomLevel - ZOOM_STEP);
        } else {
          this.zoomLevel = Math.min(MAX_ZOOM, this.zoomLevel + ZOOM_STEP);
        }

        // Apply zoom
        this.mainCamera.setZoom(this.zoomLevel);
      }
    );
  }

  handleResize() {
    // Resize the UI camera to match the main camera
    this.uiCamera.setSize(this.mainCamera.width, this.mainCamera.height);
  }

  update(cursors: Phaser.Types.Input.Keyboard.CursorKeys) {
    if (!cursors) return;

    const cameraMoveSpeed = 10 / this.zoomLevel; // Adjust speed based on zoom

    if (cursors.left.isDown) {
      this.mainCamera.scrollX -= cameraMoveSpeed;
    } else if (cursors.right.isDown) {
      this.mainCamera.scrollX += cameraMoveSpeed;
    }

    if (cursors.up.isDown) {
      this.mainCamera.scrollY -= cameraMoveSpeed;
    } else if (cursors.down.isDown) {
      this.mainCamera.scrollY += cameraMoveSpeed;
    }
  }

  centerOnMap(centerX: number, centerY: number) {
    this.mainCamera.centerOn(centerX, centerY);
  }

  setZoom(zoom: number) {
    this.zoomLevel = zoom;
    this.mainCamera.setZoom(zoom);
  }

  getZoom() {
    return this.zoomLevel;
  }

  getWorldPoint(x: number, y: number) {
    return this.mainCamera.getWorldPoint(x, y);
  }

  configureIgnoreElements(
    uiElements: Phaser.GameObjects.GameObject[],
    mapContainer: Phaser.GameObjects.Container
  ) {
    // Main camera should show the map container but ignore UI elements
    this.mainCamera.ignore(uiElements);

    // UI camera should only show UI elements and ignore the map
    this.uiCamera.ignore([mapContainer]);
  }

  cleanup() {
    // Remove the UI camera
    this.scene.cameras.remove(this.uiCamera);
  }

  isDragging() {
    return this.cameraControls.isDragging;
  }

  /**
   * Save the current camera position and zoom for the overworld view
   */
  saveOverworldCameraState() {
    if (this.isOverworld) {
      const cameraState = {
        x: this.mainCamera.scrollX,
        y: this.mainCamera.scrollY,
        zoom: this.zoomLevel,
        saved: true,
        timestamp: Date.now(),
      };

      // Save in memory
      this.overworldCameraState = cameraState;

      // Also save to Phaser registry for persistence between scene transitions
      try {
        if (this.scene && this.scene.game && this.scene.game.registry) {
          this.scene.game.registry.set("overworldCameraState", cameraState);
          console.log("Saved camera state:", {
            x: cameraState.x.toFixed(2),
            y: cameraState.y.toFixed(2),
            zoom: cameraState.zoom,
          });
        }
      } catch (error) {
        console.error("Error saving camera state:", error);
      }
    }
  }

  /**
   * Restore the saved overworld camera position and zoom
   * @returns True if a saved state was restored, false otherwise
   */
  restoreOverworldCameraState(): boolean {
    try {
      // First try to get from global Phaser registry (persists between scene transitions)
      const registryState = this.scene.game.registry.get(
        "overworldCameraState"
      );

      if (registryState && registryState.saved) {
        // Set zoom first to ensure proper positioning
        this.setZoom(registryState.zoom);

        // Then set scroll position
        this.mainCamera.setScroll(registryState.x, registryState.y);

        // Update memory state
        this.overworldCameraState = registryState;

        // Make sure we're in overworld mode
        this.isOverworld = true;

        console.log("Restored camera state:", {
          x: registryState.x.toFixed(2),
          y: registryState.y.toFixed(2),
          zoom: registryState.zoom,
        });
        return true;
      }

      // Fall back to memory if registry failed
      if (this.overworldCameraState.saved) {
        // Set zoom first to ensure proper positioning
        this.setZoom(this.overworldCameraState.zoom);

        // Then set scroll position
        this.mainCamera.setScroll(
          this.overworldCameraState.x,
          this.overworldCameraState.y
        );

        // Make sure we're in overworld mode
        this.isOverworld = true;

        console.log("Restored camera state from memory");
        return true;
      }

      return false;
    } catch (error) {
      console.error("Error restoring camera state:", error);
      return false;
    }
  }

  /**
   * Set the view mode to overworld or non-overworld and adjust zoom accordingly
   * @param isOverworld Whether the current view is the overworld
   */
  setViewMode(isOverworld: boolean) {
    // Only save the camera state if we're switching from overworld to map view
    if (this.isOverworld && !isOverworld) {
      // Check if we already have a saved state in the registry
      const existingState = this.scene.game.registry.get(
        "overworldCameraState"
      );

      // Only save if we don't have an existing state or we're not at default position
      if (
        !existingState ||
        !existingState.saved ||
        this.mainCamera.scrollX !== 0 ||
        this.mainCamera.scrollY !== 0 ||
        this.zoomLevel !== DEFAULT_ZOOM
      ) {
        this.saveOverworldCameraState();
      }
    }

    // Update the mode
    this.isOverworld = isOverworld;

    // Set appropriate zoom level based on view mode
    if (isOverworld) {
      // Don't set zoom here as we'll restore it in loadOverworldData
    } else {
      // Always use the default zoom for map views
      this.setZoom(NON_OVERWORLD_ZOOM);
      // Reset camera position for map views
      this.mainCamera.setScroll(0, 0);
    }
  }

  /**
   * Get the current view mode
   * @returns Whether the current view is in overworld mode
   */
  isInOverworldMode(): boolean {
    return this.isOverworld;
  }

  resetCamera() {
    // Reset zoom to appropriate default based on current view mode
    if (this.isOverworld) {
      this.setZoom(DEFAULT_ZOOM);
      // Clear saved state when explicitly resetting in overworld mode
      this.overworldCameraState.saved = false;
      // Also clear from global registry
      this.scene.game.registry.remove("overworldCameraState");
    } else {
      // In map view, just reset the zoom but DON'T clear the saved overworld state
      this.setZoom(NON_OVERWORLD_ZOOM);
    }

    // Reset camera position
    this.mainCamera.setScroll(0, 0);

    // Reset dragging state
    this.cameraControls.isDragging = false;
  }
}
