class ServiceError extends Error {
  constructor (service, status, body, details) {
    super(`${service} HTTP ${status}: ${details?.errorMessage || body}`)
    this.name = 'ServiceError'
    this.service = service
    this.status = status
    this.body = body
    this.code = details?.error
    this.errorCode = details?.errorCode
    this.details = details?.errorDetails
  }
}
module.exports = { ServiceError }
