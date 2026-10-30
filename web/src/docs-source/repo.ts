/** Root of the Voxos repository that contains this website. */
export const repoRootUrl = new URL("../../../", import.meta.url);

/** File URL for a repo-relative POSIX path such as `docs/wiki/Home.md`. */
export function repoFileUrl(repoPath: string): URL {
  return new URL(repoPath, repoRootUrl);
}
