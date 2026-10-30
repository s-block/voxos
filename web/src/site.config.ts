/**
 * Facts about the project. Anything that names the project or its repository
 * reads from here. The site's own URL is a deployment setting, WEB_SITE_URL;
 * pages read it from Astro's `site`.
 */
export const site = {
  name: "Voxos",
  title: "Voxos: the open-source voice AI agent for calls, SMS, WhatsApp and MCP",
  description:
    "Voxos is an open-source, self-hosted voice AI agent. Talk to it, call it, text it or WhatsApp it, and give it skills, schedules and MCP tools.",
  license: "MIT",
  repository: {
    owner: "s-block",
    name: "voxos",
    branch: "main",
  },
} as const;

export const repositoryUrl = `https://github.com/${site.repository.owner}/${site.repository.name}`;

export const githubUrls = {
  blob: (repoPath: string): string =>
    `${repositoryUrl}/blob/${site.repository.branch}/${repoPath}`,
  tree: (repoPath: string): string =>
    repoPath === ""
      ? repositoryUrl
      : `${repositoryUrl}/tree/${site.repository.branch}/${repoPath}`,
  edit: (repoPath: string): string =>
    `${repositoryUrl}/edit/${site.repository.branch}/${repoPath}`,
  license: `${repositoryUrl}/blob/${site.repository.branch}/LICENSE`,
  releases: `${repositoryUrl}/releases`,
  issues: `${repositoryUrl}/issues`,
} as const;
