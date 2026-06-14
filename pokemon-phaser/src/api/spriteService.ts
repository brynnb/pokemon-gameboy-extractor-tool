import { viewerUrl } from "./constants";

/**
 * Get the URL for a sprite image by name
 * @param spriteName The name of the sprite file (e.g., "poke_ball.png")
 * @returns The full URL to the sprite image
 */
export const getSpriteUrl = (spriteName: string): string => {
  return viewerUrl(`viewer-assets/sprites/${spriteName}`);
};
