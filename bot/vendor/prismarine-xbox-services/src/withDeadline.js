// Bound the entire operation, including credential providers that cannot be aborted.
async function withDeadline (run, { signal, timeout = 15000 } = {}, lifetime) {
  const controller = new AbortController()
  const combined = AbortSignal.any([controller.signal, ...[signal, lifetime].filter(Boolean)])
  const timer = setTimeout(() => controller.abort(new Error('Operation timed out')), timeout)
  let onAbort
  const cancelled = new Promise((resolve, reject) => {
    onAbort = () => reject(combined.reason)
    if (combined.aborted) onAbort()
    else combined.addEventListener('abort', onAbort, { once: true })
  })
  try {
    return await Promise.race([Promise.resolve().then(() => {
      combined.throwIfAborted()
      return run(combined)
    }), cancelled])
  } finally {
    clearTimeout(timer)
    combined.removeEventListener('abort', onAbort)
  }
}
module.exports = { withDeadline }
