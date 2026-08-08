import { Link, Route, Routes } from 'react-router-dom'

import { Layout } from './components/Layout'
import { ExamDetail } from './features/exams/ExamDetail'
import { VerifierConsole } from './features/verification/VerifierConsole'

function Home() {
  return (
    <>
      <p className="mb-2 text-gray-600">Dashboard coming soon.</p>
      <Link to="/verify" className="text-sm underline">
        Verifier console
      </Link>
    </>
  )
}

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/exams/:slug" element={<ExamDetail />} />
        <Route path="/verify" element={<VerifierConsole />} />
      </Routes>
    </Layout>
  )
}

export default App
