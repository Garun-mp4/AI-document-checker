import React, { Component, type ErrorInfo, type ReactNode } from 'react'
import ReactDOM from 'react-dom/client'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import './styles.css'
import App from './App'

interface AppErrorBoundaryState { hasError: boolean; message: string }

class AppErrorBoundary extends Component<{ children: ReactNode }, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { hasError: false, message: '' }

  static getDerivedStateFromError(error: unknown): AppErrorBoundaryState {
    return { hasError: true, message: error instanceof Error ? error.message : 'Неизвестная ошибка интерфейса' }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error('Необработанная ошибка интерфейса', error, info.componentStack)
  }

  render() {
    if (!this.state.hasError) return this.props.children
    return (
      <main className="fatal-error-page" role="alert">
        <div className="fatal-error-card">
          <strong>Не удалось отобразить рабочую область</strong>
          <p>Документ сохранён. Повторите открытие чата или перезагрузите интерфейс.</p>
          <small>{this.state.message}</small>
          <button type="button" className="button button-dark" onClick={() => window.location.reload()}>Перезагрузить</button>
        </div>
      </main>
    )
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppErrorBoundary><App /></AppErrorBoundary>
  </React.StrictMode>,
)
