import { useEffect, useRef } from 'react'
import { normalizeRms, rmsFromTimeDomainData } from '../microphoneLevel.mjs'

const BAR_COUNT = 29
const SAMPLE_INTERVAL_MS = 36
const REDUCED_SAMPLE_INTERVAL_MS = 180
const QUIET_SCALE = 0.12

type VoiceLevelMeterProps = {
  analyser: AnalyserNode | null
  recording: boolean
}

export function VoiceLevelMeter({ analyser, recording }: VoiceLevelMeterProps) {
  const barsRef = useRef<Array<HTMLSpanElement | null>>([])

  useEffect(() => {
    const resetBars = () => {
      for (const bar of barsRef.current) {
        if (bar) bar.style.transform = `scaleY(${QUIET_SCALE})`
      }
    }

    if (!recording || !analyser) {
      resetBars()
      return
    }

    const samples = new Uint8Array(analyser.fftSize)
    const history = Array.from({ length: BAR_COUNT }, () => 0)
    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    let reducedMotion = motionQuery.matches
    let animationFrame = 0
    let timer = 0
    let disposed = false

    const renderLevels = () => {
      if (reducedMotion) {
        const level = history.at(-1) ?? 0
        for (const bar of barsRef.current) {
          if (bar) bar.style.transform = `scaleY(${QUIET_SCALE + level * 0.88})`
        }
        return
      }

      history.forEach((level, index) => {
        const bar = barsRef.current[index]
        if (bar) bar.style.transform = `scaleY(${QUIET_SCALE + level * 0.88})`
      })
    }

    const handleMotionChange = (event: MediaQueryListEvent) => {
      reducedMotion = event.matches
      history.fill(history.at(-1) ?? 0)
      renderLevels()
    }

    const update = () => {
      if (disposed) return
      analyser.getByteTimeDomainData(samples)
      history.shift()
      history.push(normalizeRms(rmsFromTimeDomainData(samples)))
      renderLevels()
      timer = window.setTimeout(() => {
        animationFrame = window.requestAnimationFrame(update)
      }, reducedMotion ? REDUCED_SAMPLE_INTERVAL_MS : SAMPLE_INTERVAL_MS)
    }

    motionQuery.addEventListener('change', handleMotionChange)
    animationFrame = window.requestAnimationFrame(update)
    return () => {
      disposed = true
      window.clearTimeout(timer)
      window.cancelAnimationFrame(animationFrame)
      motionQuery.removeEventListener('change', handleMotionChange)
      resetBars()
    }
  }, [analyser, recording])

  return (
    <div className="voice-level-meter" aria-hidden="true">
      {Array.from({ length: BAR_COUNT }, (_, index) => (
        <span
          className="voice-level-meter-bar"
          key={index}
          ref={(element) => { barsRef.current[index] = element }}
        />
      ))}
    </div>
  )
}
