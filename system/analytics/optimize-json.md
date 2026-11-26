# Safe JSON response optimizer

A dependency-free helper for reducing the size of large JSON responses from GitHub or other tools before analysis.

## Usage

Pipe JSON into the helper. It reads only standard input and writes the result to standard output:

```bash
gh api repos/OWNER/REPO/issues --paginate | python system/analytics/optimize_json.py --stats
```

Large arrays (5 or more items by default) are returned as a count, a schema, and two examples. Small arrays are cleaned of empty and duplicate entries. To extract specific fields first:

```bash
gh api repos/OWNER/REPO/issues --paginate | python system/analytics/optimize_json.py --path '$[*].title'
```

Supported paths are intentionally limited to `$.field.subfield`, `$[index]`, and `$[*].field`. This prevents arbitrary code execution and avoids a new package dependency. Adjust `--array-threshold`, `--sample-size`, `--max-depth`, or `--max-chars` as needed. If output remains too large, the helper returns a schema hint rather than silently dropping content.

## Safety boundary

This is a local data transformer only. It does not fetch URLs, read file paths, add request headers, or contact external services. Do not pipe secrets or credentials into it unless they are needed for the analysis; output and source data are not saved by the helper.

The third-party Fetch MCP server is not installed here. Its network fetcher currently accepts caller-provided URLs and headers and follows redirects; its JSON optimizer also accepts local file paths. Those behaviors need an isolated runtime and additional controls before deployment.
