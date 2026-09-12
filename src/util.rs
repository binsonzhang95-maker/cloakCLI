use anyhow::{bail, Result};
use std::path::{Component, Path, PathBuf};

/// Strict name for profiles / skills: 1-64 chars, start alphanumeric, then [A-Za-z0-9._-].
pub fn validate_name(name: &str, kind: &str) -> Result<()> {
    let ok = !name.is_empty()
        && name.len() <= 64
        && name.chars().enumerate().all(|(i, c)| {
            if i == 0 {
                c.is_ascii_alphanumeric()
            } else {
                c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-')
            }
        })
        && !name.contains("..")
        && !name.contains('/')
        && !name.contains('\\');
    if !ok {
        bail!(
            "Invalid {kind} name '{name}'. Use 1-64 chars: letters, digits, . _ - (start alphanumeric); no path separators."
        );
    }
    Ok(())
}

/// Ensure `path` resolves under `root` (canonicalize + prefix check).
/// Non-existent paths: canonicalize parent and join the final component.
pub fn ensure_under_root(root: &Path, path: &Path) -> Result<PathBuf> {
    let root_canon = root
        .canonicalize()
        .map_err(|e| anyhow::anyhow!("canonicalize root {}: {e}", root.display()))?;

    let candidate = if path.is_absolute() {
        path.to_path_buf()
    } else {
        root.join(path)
    };

    // Reject obvious traversal components before canonicalize
    for c in candidate.components() {
        if matches!(c, Component::ParentDir) {
            // still allow if final resolve stays under root — check after
            break;
        }
    }

    let resolved = if candidate.exists() {
        candidate.canonicalize().map_err(|e| {
            anyhow::anyhow!("canonicalize {}: {e}", candidate.display())
        })?
    } else {
        // Resolve as far as possible
        let mut cur = PathBuf::new();
        let components: Vec<_> = candidate.components().collect();
        for (i, comp) in components.iter().enumerate() {
            let next = if cur.as_os_str().is_empty() {
                PathBuf::from(comp.as_os_str())
            } else {
                cur.join(comp)
            };
            if next.exists() {
                cur = next;
            } else {
                // canonicalize existing prefix, then append remaining
                let base = if cur.as_os_str().is_empty() {
                    PathBuf::from("/")
                } else {
                    cur.canonicalize().unwrap_or(cur)
                };
                let mut out = base;
                for rem in &components[i..] {
                    out.push(rem);
                }
                // Normalize .. manually
                return normalize_and_check(&root_canon, &out);
            }
        }
        cur.canonicalize().unwrap_or(cur)
    };

    if !resolved.starts_with(&root_canon) {
        bail!(
            "path escapes project root: {} (root={})",
            resolved.display(),
            root_canon.display()
        );
    }
    Ok(resolved)
}

fn normalize_and_check(root_canon: &Path, path: &Path) -> Result<PathBuf> {
    let mut out = PathBuf::new();
    for c in path.components() {
        match c {
            Component::RootDir => out.push("/"),
            Component::CurDir => {}
            Component::ParentDir => {
                if !out.pop() {
                    bail!("path escapes project root: {}", path.display());
                }
            }
            Component::Normal(s) => out.push(s),
            Component::Prefix(p) => out.push(p.as_os_str()),
        }
    }
    if !out.starts_with(root_canon) {
        bail!(
            "path escapes project root: {} (root={})",
            out.display(),
            root_canon.display()
        );
    }
    Ok(out)
}

/// Redact credentials in proxy URLs: `scheme://user:pass@host` → `scheme://***:***@host`
pub fn redact_proxy(proxy: &str) -> String {
    // Look for :// then optional userinfo before @
    if let Some(scheme_end) = proxy.find("://") {
        let after = scheme_end + 3;
        if let Some(at) = proxy[after..].find('@') {
            let userinfo = &proxy[after..after + at];
            if userinfo.contains(':') || !userinfo.is_empty() {
                let rest = &proxy[after + at..]; // includes @host...
                return format!("{}://***:***{}", &proxy[..scheme_end], rest);
            }
        }
    }
    proxy.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redact_basic() {
        assert_eq!(
            redact_proxy("http://user:secret@127.0.0.1:7890"),
            "http://***:***@127.0.0.1:7890"
        );
        assert_eq!(redact_proxy("http://127.0.0.1:7890"), "http://127.0.0.1:7890");
    }

    #[test]
    fn name_rejects_traversal() {
        assert!(validate_name("../x", "skill").is_err());
        assert!(validate_name("hello", "skill").is_ok());
    }
}
