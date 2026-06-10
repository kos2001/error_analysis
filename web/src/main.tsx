import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import FailureAnalysis from './FailureAnalysis.tsx'

function Root() {
  return (
    <div className="h-screen flex flex-col">
      <nav className="flex items-center gap-1 px-4 h-11 bg-slate-900 text-slate-300 text-sm shrink-0">
        <span className="font-semibold text-white mr-3">LSI Error Analysis</span>
        <span className="px-3 py-1 rounded bg-indigo-600 text-white">고장 분석 추천</span>
      </nav>
      <div className="flex-1 min-h-0">
        <FailureAnalysis />
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
