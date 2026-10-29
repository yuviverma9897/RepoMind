import { useMemo, useState } from 'react'

// Convert the flat [{path, type}] list from the API into a sorted nested tree.
// Parent folders are created implicitly, so partial (truncated) lists still nest correctly.
function buildTree(files) {
  const root = { children: new Map() }

  for (const { path, type } of files) {
    const parts = path.split('/')
    let node = root
    parts.forEach((part, i) => {
      const isLast = i === parts.length - 1
      if (!node.children.has(part)) {
        node.children.set(part, {
          name: part,
          path: parts.slice(0, i + 1).join('/'),
          type: isLast ? type : 'directory',
          children: new Map(),
        })
      }
      node = node.children.get(part)
    })
  }

  const toSortedArray = (node) =>
    [...node.children.values()]
      .sort((a, b) =>
        a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'directory' ? -1 : 1,
      )
      .map((child) => ({ ...child, children: toSortedArray(child) }))

  return toSortedArray(root)
}

function FolderIcon({ open }) {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0 fill-cyan-400/80" aria-hidden="true">
      {open ? (
        <path d="M2 5a2 2 0 012-2h4l2 2h6a2 2 0 012 2v1H6.5a2 2 0 00-1.9 1.4L2 16V5zm2.7 5.3A1 1 0 015.6 10H19l-2.4 6.3a1 1 0 01-.9.7H3l1.7-6.7z" />
      ) : (
        <path d="M2 5a2 2 0 012-2h4l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V5z" />
      )}
    </svg>
  )
}

function FileIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0 fill-slate-500" aria-hidden="true">
      <path d="M5 2a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7.4a1 1 0 00-.3-.7l-4.4-4.4a1 1 0 00-.7-.3H5zm6 1.5L15.5 8H12a1 1 0 01-1-1V3.5z" />
    </svg>
  )
}

function TreeNode({ node, depth, selectedPath, onSelectFile }) {
  const [open, setOpen] = useState(false)
  const indent = { paddingLeft: `${depth * 16 + 8}px` }

  if (node.type === 'file') {
    const selected = node.path === selectedPath
    return (
      <li>
        <button
          type="button"
          onClick={() => onSelectFile(node.path)}
          aria-current={selected ? 'true' : undefined}
          title={node.path}
          style={indent}
          className={`flex w-full items-center gap-2 rounded py-1 pr-2 text-left ${
            selected
              ? 'bg-cyan-500/15 text-cyan-200'
              : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
          }`}
        >
          <span className="w-3 shrink-0" />
          <FileIcon />
          <span className="truncate">{node.name}</span>
        </button>
      </li>
    )
  }

  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        style={indent}
        className="flex w-full items-center gap-2 rounded py-1 pr-2 text-left text-slate-200 hover:bg-slate-800/60"
      >
        <span className={`w-3 shrink-0 text-[10px] text-slate-500 transition-transform ${open ? 'rotate-90' : ''}`}>▶</span>
        <FolderIcon open={open} />
        <span className="truncate">{node.name}</span>
        {node.children.length === 0 && <span className="text-xs text-slate-600">(empty)</span>}
      </button>
      {open && node.children.length > 0 && (
        <ul>
          {node.children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              onSelectFile={onSelectFile}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

export default function FileTree({ files, tree, selectedPath, onSelectFile }) {
  const nodes = useMemo(() => buildTree(files), [files])

  return (
    <section className="flex min-h-0 flex-1 flex-col rounded-xl border border-slate-800 bg-slate-900/60">
      <div className="flex items-center justify-between border-b border-slate-800 px-5 py-3">
        <h3 className="text-sm font-semibold text-slate-200">Explorer</h3>
        <span className="font-mono text-xs text-slate-500">
          {tree.file_count.toLocaleString()} files
        </span>
      </div>

      {tree.truncated && (
        <div className="border-b border-amber-500/20 bg-amber-500/10 px-5 py-2 text-sm text-amber-300">
          Very large repository: GitHub returned a partial tree, so some files are missing.
        </div>
      )}

      {tree.empty ? (
        <p className="px-5 py-10 text-center text-sm text-slate-500">This repository is empty — it has no files yet.</p>
      ) : (
        <ul className="min-h-0 flex-1 overflow-auto p-2 font-mono text-[13px]">
          {nodes.map((node) => (
            <TreeNode
              key={node.path}
              node={node}
              depth={0}
              selectedPath={selectedPath}
              onSelectFile={onSelectFile}
            />
          ))}
        </ul>
      )}
    </section>
  )
}
