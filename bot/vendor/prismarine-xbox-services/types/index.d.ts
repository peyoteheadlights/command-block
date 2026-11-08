import { EventEmitter } from 'events'

/** Implemented by prismarine-auth's Authflow. Authentication remains caller-owned. */
export interface XboxTokenProvider {
  getXboxToken(relyingParty?: string, forceRefresh?: boolean): Promise<{ userHash: string; XSTSToken: string }>
}
export interface OperationOptions {
  timeout?: number
  signal?: AbortSignal
}
export interface XboxOptions {
  titleId?: string
  scid?: string
  templateName?: string
  timeout?: number
  cleanupTimeout?: number
}
export type UserIdentifier = 'me' | { xuid: string; gamertag?: never } | { gamertag: string; xuid?: never }
export interface RequestOptions extends OperationOptions {
  data?: unknown
  headers?: Record<string, string>
  contractVersion?: string
}
export interface Profile {
  xuid: string
  gamertag?: string
  displayName?: string
  avatarUrl?: string
}
export interface SessionProperties {
  system?: Record<string, unknown>
  custom?: Record<string, unknown>
}
export interface SessionMember {
  constants?: { system?: { xuid?: string; [key: string]: unknown }; [key: string]: unknown }
  properties?: Record<string, unknown>
  [key: string]: unknown
}
export interface SessionDocument {
  members?: Record<string, SessionMember>
  properties: SessionProperties
  [key: string]: unknown
}
export interface SessionReference {
  scid: string
  templateName: string
  name: string
}
export interface ActivityHandle {
  sessionRef: SessionReference
  [key: string]: unknown
}
export interface CreateSessionOptions extends OperationOptions {
  name?: string
  properties?: SessionProperties | ((context: { profile: Profile }) => SessionProperties | Promise<SessionProperties>)
}
export class XboxClient {
  constructor(authflow: XboxTokenProvider, options?: XboxOptions)
  request<T = unknown>(method: string, url: string, options?: RequestOptions): Promise<T | undefined>
  abortPending(): void
  getProfile(identifier?: UserIdentifier, options?: OperationOptions): Promise<Profile>
  getActivityHandles(xuid: string, options?: OperationOptions): Promise<ActivityHandle[]>
  getSession(name: string, options?: OperationOptions): Promise<SessionDocument>
  updateSession(name: string, payload: Record<string, unknown>, options?: OperationOptions): Promise<unknown>
  setActivity(name: string, options?: OperationOptions): Promise<unknown>
  sendInvite(name: string, xuid: string, options?: OperationOptions): Promise<unknown>
  createSession(options?: CreateSessionOptions): Promise<XboxSession>
  joinSession(name: string, options?: OperationOptions): Promise<XboxSession>
}
export class XboxSession extends EventEmitter<{
  changed: [current: SessionDocument, previous: SessionDocument]
  memberJoin: [member: SessionMember]
  memberLeave: [member: SessionMember]
  propertiesChanged: [properties: SessionProperties]
  error: [error: Error]
}> {
  private constructor()
  readonly name: string
  readonly state: 'opening' | 'open' | 'closing' | 'closed'
  readonly current: SessionDocument
  setActivity(options?: OperationOptions): Promise<void>
  get(options?: OperationOptions): Promise<SessionDocument>
  updateProperties(properties: SessionProperties, options?: OperationOptions): Promise<void>
  invite(identifier: UserIdentifier, options?: OperationOptions): Promise<void>
  close(): Promise<void>
}
export class XboxRTASubscription<T = unknown> extends EventEmitter {
  private constructor()
  readonly uri: string
  readonly initialData: T
  readonly closed: boolean
  close(): Promise<void>
  on(event: 'ready' | 'data', listener: (data: T) => void): this
  on(event: string | symbol, listener: (...args: any[]) => void): this
}
export class SocketError extends Error {
  constructor(message?: string, options?: ErrorOptions)
}
export class SocketClosedError extends SocketError {
  constructor(message?: string)
}
export class SocketNotConnectedError extends SocketError {
  constructor()
}
export class SocketAlreadyConnectedError extends SocketError {
  constructor()
}
export class RTARequestError extends SocketError {
  constructor(status: number)
  readonly status: number
  readonly code: string
}
export class XboxRTASocket extends EventEmitter {
  constructor(client: XboxClient)
  connect(options?: OperationOptions): Promise<void>
  reconnect(): Promise<void>
  subscribe<T = unknown>(uri: string, options?: OperationOptions): Promise<XboxRTASubscription<T>>
  close(): Promise<void>
  on(event: 'resync', listener: () => void): this
  on(event: 'disconnect', listener: (code: number, reason: string) => void): this
  on(event: 'close', listener: () => void): this
  on(event: 'error', listener: (error: Error) => void): this
  on(event: string | symbol, listener: (...args: any[]) => void): this
}
export class ServiceError extends Error {
  constructor(service: string, status: number, body: string, details?: { error?: string; errorMessage?: string; errorCode?: number; errorDetails?: Record<string, unknown> })
  readonly service: string
  readonly status: number
  readonly body: string
  readonly code?: string
  readonly errorCode?: number
  readonly details?: Record<string, unknown>
}
export interface PlayFabCredentials {
  SessionTicket?: string
  EntityToken?: { EntityToken: string }
}
export interface PlayFabOptions {
  titleId: string
  timeout?: number
}
export interface PlayFabRequestOptions extends OperationOptions {
  auth?: 'session' | 'entity'
}
export interface CloudScriptOptions {
  functionName: string
  functionParameter?: unknown
  generatePlayStreamEvent?: boolean
}
export interface CloudScriptResult {
  FunctionResult?: unknown
  Error?: { Error?: string; Message?: string; StackTrace?: string }
  [key: string]: unknown
}
export interface InventoryResult {
  Inventory?: Array<Record<string, unknown>>
  VirtualCurrency?: Record<string, number>
  [key: string]: unknown
}
export class PlayFabClient {
  constructor(getCredentials: () => PlayFabCredentials | Promise<PlayFabCredentials>, options: PlayFabOptions)
  getTitleData(data?: { keys?: string[] }, options?: OperationOptions): Promise<{ Data?: Record<string, string> } | undefined>
  getUserInventory(options?: OperationOptions): Promise<InventoryResult | undefined>
  executeCloudScript(data: CloudScriptOptions, options?: OperationOptions): Promise<CloudScriptResult | undefined>
  request<T = unknown>(path: string, data?: unknown, options?: PlayFabRequestOptions): Promise<T | undefined>
  abortPending(): void
}
