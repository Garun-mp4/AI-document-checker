import { useEffect, useRef, useState } from 'react'
import { buildsMatch, FRONTEND_BUILD_INFO, isVerifiedBuild, type BuildInfo } from './buildInfo'

export type BuildVersionCheck =
  | { kind: 'checking' }
  | { kind: 'matched'; frontend: BuildInfo; manifest: BuildInfo; api: BuildInfo }
  | { kind: 'mismatch'; frontend: BuildInfo; manifest: BuildInfo; api: BuildInfo }
  | { kind: 'unverified'; frontend: BuildInfo; manifest: BuildInfo; api: BuildInfo }
  | { kind: 'unavailable' }

export function useBuildVersion(): { check: BuildVersionCheck; recheck: () => void } {
  const [check, setCheck] = useState<BuildVersionCheck>({ kind: 'checking' })
  const recheckRef = useRef<() => void>(() => undefined)

  useEffect(() => {
    let active = true
    let inFlight = false

    const refresh = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const [manifestResponse, apiResponse] = await Promise.all([
          fetch('/build-info.json', { cache: 'no-store' }),
          fetch('/api/v1/version', { cache: 'no-store' }),
        ])
        if (!manifestResponse.ok || !apiResponse.ok) throw new Error('version endpoint unavailable')
        const [manifest, api] = await Promise.all([
          manifestResponse.json() as Promise<BuildInfo>,
          apiResponse.json() as Promise<BuildInfo>,
        ])
        if (!active) return
        const builds = [FRONTEND_BUILD_INFO, manifest, api]
        if (!builds.every(isVerifiedBuild)) {
          setCheck({ kind: 'unverified', frontend: FRONTEND_BUILD_INFO, manifest, api })
        } else if (!buildsMatch(FRONTEND_BUILD_INFO, manifest) || !buildsMatch(manifest, api)) {
          setCheck({ kind: 'mismatch', frontend: FRONTEND_BUILD_INFO, manifest, api })
        } else {
          setCheck({ kind: 'matched', frontend: FRONTEND_BUILD_INFO, manifest, api })
        }
      } catch {
        if (active) setCheck({ kind: 'unavailable' })
      } finally {
        inFlight = false
      }
    }

    recheckRef.current = () => { void refresh() }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 45_000)
    const onFocus = () => { void refresh() }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void refresh()
    }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      active = false
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return { check, recheck: () => recheckRef.current() }
}
