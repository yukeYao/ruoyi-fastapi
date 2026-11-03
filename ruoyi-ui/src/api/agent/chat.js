import request, { isRelogin } from '@/utils/request'
import { getToken } from '@/utils/auth'
import useUserStore from '@/store/modules/user'
import { ElMessageBox } from 'element-plus'

// 智能助手一次请求可能包含多轮模型调用和工具调用（审批后还要让模型总结），
// request.js 默认 10 秒超时不够用，这里单独设置较长的超时
const AGENT_TIMEOUT = 180000

// 发送消息（普通请求）
export function sendMessage(data) {
  return request({
    url: '/agent/chat',
    method: 'post',
    data: data,
    timeout: AGENT_TIMEOUT
  })
}

// 查询当前用户的会话列表
export function listConversation(query) {
  return request({
    url: '/agent/conversation/list',
    method: 'get',
    params: query
  })
}

// 查询会话的消息历史（含待审批操作）
export function getMessages(conversationId) {
  return request({
    url: '/agent/conversation/' + conversationId + '/messages',
    method: 'get'
  })
}

// 查询审批操作详情（状态以服务端 agent_action 为准）
export function getAction(actionId) {
  return request({
    url: '/agent/action/' + actionId,
    method: 'get'
  })
}

// 批准操作（普通请求）：只提交 actionId，不提交任何参数
export function approveAction(actionId) {
  return request({
    url: '/agent/action/' + actionId + '/approve',
    method: 'post',
    timeout: AGENT_TIMEOUT
  })
}

// 拒绝操作（普通请求）
export function rejectAction(actionId) {
  return request({
    url: '/agent/action/' + actionId + '/reject',
    method: 'post',
    timeout: AGENT_TIMEOUT
  })
}

// ---------------------------------------------------------------------------
// SSE：axios 不支持逐段读取响应，这里用 fetch。后端在开始推流之前完成全部校验，
// 出错时返回 HTTP 200 + JSON 的 {code, msg}，所以要先看 content-type 再决定怎么读。
// ---------------------------------------------------------------------------

// 流式发送消息
export function streamChat(data, onEvent, signal) {
  return postStream('/agent/chat/stream', data, onEvent, signal)
}

// 流式批准 / 拒绝：decision 为 'approve' 或 'reject'
export function streamDecide(actionId, decision, onEvent, signal) {
  return postStream('/agent/action/' + actionId + '/' + decision + '/stream', null, onEvent, signal)
}

// 与 request.js 的 401 处理保持一致：复用同一个 isRelogin 标记，避免和 axios 同时弹出两个对话框
function handleRelogin() {
  if (isRelogin.show) {
    return
  }
  isRelogin.show = true
  ElMessageBox.confirm('登录状态已过期，您可以继续留在该页面，或者重新登录', '系统提示', { confirmButtonText: '重新登录', cancelButtonText: '取消', type: 'warning' }).then(() => {
    isRelogin.show = false
    useUserStore().logOut().then(() => {
      location.href = '/index'
    })
  }).catch(() => {
    isRelogin.show = false
  })
}

// 业务错误统一抛出带 code 的 Error，页面按纯文本显示 msg
function businessError(body) {
  const code = body.code || 500
  if (code === 401) {
    handleRelogin()
  }
  const error = new Error(body.msg || '操作失败')
  error.code = code
  return error
}

async function postStream(url, data, onEvent, signal) {
  const headers = { 'Content-Type': 'application/json;charset=utf-8' }
  if (getToken()) {
    headers['Authorization'] = 'Bearer ' + getToken()
  }
  let resp
  try {
    resp = await fetch(import.meta.env.VITE_APP_BASE_API + url, {
      method: 'POST',
      headers: headers,
      body: data ? JSON.stringify(data) : undefined,
      signal: signal
    })
  } catch (e) {
    if (e.name === 'AbortError') {
      throw e
    }
    throw new Error('后端接口连接异常')
  }
  if (!resp.ok) {
    throw new Error('系统接口' + resp.status + '异常')
  }
  const contentType = resp.headers.get('content-type') || ''
  if (!contentType.includes('text/event-stream')) {
    // 开始推流之前的错误（未登录、无权限、正忙、操作已处理等）：HTTP 200 + JSON
    const body = await resp.json()
    throw businessError(body)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) {
      break
    }
    buffer += decoder.decode(value, { stream: true })
    // SSE 以空行分隔事件；最后一段可能不完整，留到下次拼接
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop()
    for (const block of blocks) {
      dispatch(block, onEvent)
    }
  }
  if (buffer.trim()) {
    dispatch(buffer, onEvent)
  }
}

function dispatch(block, onEvent) {
  let event = 'message'
  const dataLines = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim())
    }
  }
  if (!dataLines.length) {
    return
  }
  let payload
  try {
    payload = JSON.parse(dataLines.join('\n'))
  } catch (e) {
    return
  }
  onEvent(event, payload)
}
