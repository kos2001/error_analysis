import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import FailureAnalysis from './FailureAnalysis.tsx'

function Root() {
  const [tab, setTab] = useState<'reco' | 'chat'>('reco')
  return (
    <div className="h-screen flex flex-col">
      <nav className="flex items-center gap-1 px-4 h-11 bg-slate-900 text-slate-300 text-sm shrink-0">
        <span className="font-semibold text-white mr-3">LSI Error Analysis</span>
        <button onClick={() => setTab('reco')}
          className={`px-3 py-1 rounded ${tab === 'reco' ? 'bg-indigo-600 text-white' : 'hover:bg-slate-800'}`}>
          고장 분석 추천
        </button>
        <button onClick={() => setTab('chat')}
          className={`px-3 py-1 rounded ${tab === 'chat' ? 'bg-indigo-600 text-white' : 'hover:bg-slate-800'}`}>
          지원 챗봇
        </button>
      </nav>
      <div className="flex-1 min-h-0">
        {tab === 'reco' ? <FailureAnalysis /> : <App />}
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
