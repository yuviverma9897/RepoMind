import { useEffect, useState } from 'react'
import { getHealth } from '../api'

const STYLES = {
  checking: { dot: 'bg-amber-400', label: 'Checking backend…' },
  online: { dot: 'bg-emerald-400', label: 'Backend online' },
  offline: { dot: 'bg-rose-500', label: 'Backend offline' },
}

export default function BackendStatus() {
  const [status, setStatus] = useState('checking')
  const [version, setVersion] = useState(null)

  useEffect(() => {
    getHealth()
      .then((data) => {
        setStatus('online')
        setVersion(data.version)
      })
      .catch(() => setStatus('offline'))
  }, [])

  const { dot, label } = STYLES[status]

  return (
    <div className="flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900/60 px-3 py-1 font-mono text-xs text-slate-400">
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      {label}
      {version && <span className="text-slate-600">v{version}</span>}
    </div>
  )
}
