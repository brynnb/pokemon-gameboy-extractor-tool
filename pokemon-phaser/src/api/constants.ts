const baseUrl = import.meta.env.BASE_URL || "./";

export const viewerUrl = (path: string): string => {
  const normalizedPath = path.replace(/^\/+/, "");
  return `${baseUrl}${normalizedPath}`;
};

export const fetchViewerJson = async <T>(path: string): Promise<T> => {
  const url = viewerUrl(path);
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(
      `Failed to fetch ${url}: ${response.status} ${response.statusText}`
    );
  }

  return (await response.json()) as T;
};
