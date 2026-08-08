import { Route, Routes } from 'react-router-dom'

import { Layout } from './components/Layout'
import { CalendarView } from './features/exams/CalendarView'
import { ExamDetail } from './features/exams/ExamDetail'
import { ExamList } from './features/exams/ExamList'
import { VerifierConsole } from './features/verification/VerifierConsole'

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<ExamList />} />
        <Route path="/calendar" element={<CalendarView />} />
        <Route path="/exams/:slug" element={<ExamDetail />} />
        <Route path="/verify" element={<VerifierConsole />} />
      </Routes>
    </Layout>
  )
}

export default App
