<template>
  <div class="app-container agent-chat">
    <el-row :gutter="16">
      <!-- 会话列表 -->
      <el-col :xs="24" :sm="7" :md="6">
        <el-card shadow="never" class="conversation-card">
          <template #header>
            <div class="card-header">
              <span>会话</span>
              <el-button type="primary" link icon="Plus" :disabled="running" @click="newConversation">新会话</el-button>
            </div>
          </template>
          <el-scrollbar max-height="calc(100vh - 230px)">
            <div
              v-for="conv in conversations"
              :key="conv.conversationId"
              class="conversation-item"
              :class="{ active: conv.conversationId === conversationId }"
              @click="openConversation(conv.conversationId)"
            >
              <div class="conversation-title">{{ conv.title || '（无标题）' }}</div>
              <div class="conversation-time">{{ conv.updateTime }}</div>
            </div>
            <el-empty v-if="!conversations.length" description="暂无会话" :image-size="60" />
          </el-scrollbar>
        </el-card>
      </el-col>

      <!-- 对话区 -->
      <el-col :xs="24" :sm="17" :md="18">
        <el-card shadow="never" class="chat-card">
          <template #header>
            <div class="card-header">
              <span>智能助手</span>
              <span class="header-right">
                <el-switch v-model="useStream" :disabled="running" active-text="流式输出" />
              </span>
            </div>
          </template>

          <el-scrollbar ref="scrollRef" height="calc(100vh - 360px)" class="message-area">
            <el-empty v-if="!items.length" description="可以询问系统规则和权限配置，查询用户、角色、部门、登录日志、操作日志和在线会话；停用账号、解锁、强制下线需要你审批后才会执行" :image-size="80" />

            <div v-for="item in items" :key="item.key" class="message-row" :class="'row-' + item.kind">
              <!-- 用户消息 -->
              <div v-if="item.kind === 'user'" class="bubble bubble-user">{{ item.text }}</div>

              <!-- 模型回复：纯文本，不渲染 Markdown/HTML/链接 -->
              <div v-else-if="item.kind === 'assistant'" class="bubble bubble-assistant">
                <span>{{ item.text }}</span><span v-if="item.streaming" class="cursor">▍</span>
              </div>

              <!-- 知识库来源：只显示服务端校验过的 sources（本轮检索实际返回的分块），不解析回复里的引用 -->
              <div v-else-if="item.kind === 'sources'" class="sources">
                <template v-if="item.knowledge.sources && item.knowledge.sources.length">
                  <div class="sources-title">来源（知识库）</div>
                  <div v-for="s in item.knowledge.sources" :key="s.chunkId" class="source-item">
                    <span>{{ s.title }}</span><span v-if="s.section" class="source-section"> · {{ s.section }}</span>
                    <span class="source-id">[{{ s.chunkId }}]</span>
                  </div>
                </template>
                <el-tag v-else-if="KNOWLEDGE_STATUS[item.knowledge.status]" size="small" :type="KNOWLEDGE_STATUS[item.knowledge.status].type">
                  {{ KNOWLEDGE_STATUS[item.knowledge.status].label }}
                </el-tag>
                <div v-if="item.knowledge.sources && item.knowledge.sources.length" class="source-note">
                  来源只表示内容来自本轮知识库检索，不代表回答已被验证为真
                </div>
                <div v-if="item.knowledge.invalidCitations" class="source-warning">
                  回复中有 {{ item.knowledge.invalidCitations }} 处引用不是本轮知识库检索返回的内容，无法核实，未列入来源
                </div>
                <div v-if="item.knowledge.suspiciousCitations" class="source-warning">
                  存在无法核实的引用格式（{{ item.knowledge.suspiciousCitations }} 处），未列入来源
                </div>
                <!-- 检索到但回复没有引用的片段：与来源分开，明确不是回答依据 -->
                <template v-if="item.knowledge.references && item.knowledge.references.length">
                  <div class="references-title">检索到但未被引用的参考资料（不是回答依据）</div>
                  <div v-for="r in item.knowledge.references" :key="r.chunkId" class="reference-item">
                    <span>{{ r.title }}</span><span v-if="r.section" class="source-section"> · {{ r.section }}</span>
                    <span class="source-id">[{{ r.chunkId }}]</span>
                  </div>
                </template>
              </div>

              <!-- 工具调用 -->
              <div v-else-if="item.kind === 'tool_call'" class="step">
                <el-tag size="small" type="info">调用工具</el-tag>
                <span class="step-name">{{ item.name }}</span>
                <span class="step-args">{{ item.args }}</span>
              </div>

              <!-- 工具结果：原始 JSON 按纯文本显示，默认折叠 -->
              <div v-else-if="item.kind === 'tool_result'" class="step">
                <el-tag size="small" :type="item.ok ? 'success' : 'danger'">{{ item.ok ? '工具结果' : '工具未执行' }}</el-tag>
                <span class="step-name">{{ item.name }}</span>
                <span v-if="item.statusText" class="step-status">{{ item.statusText }}</span>
                <span v-else-if="item.error" class="step-error">{{ item.error }}</span>
                <el-button link type="primary" size="small" @click="item.expanded = !item.expanded">{{ item.expanded ? '收起' : '详情' }}</el-button>
                <pre v-if="item.expanded" class="step-detail">{{ item.content }}</pre>
              </div>

              <!-- 审批卡片：只显示服务端生成的预览和 agent_action 的状态 -->
              <div v-else-if="item.kind === 'approval'" class="approval-card">
                <div class="approval-head">
                  <span class="approval-title">{{ item.action.preview.operation || item.action.toolName }}</span>
                  <el-tag :type="statusOf(item.action).type">{{ statusOf(item.action).label }}</el-tag>
                </div>
                <el-descriptions :column="1" size="small" border>
                  <el-descriptions-item label="目标用户">
                    {{ targetText(item.action) }}
                  </el-descriptions-item>
                  <el-descriptions-item label="当前状态">{{ item.action.preview.currentState }}</el-descriptions-item>
                  <el-descriptions-item label="变更后">{{ item.action.preview.afterState }}</el-descriptions-item>
                  <el-descriptions-item label="所需权限">{{ item.action.perm }}</el-descriptions-item>
                  <el-descriptions-item label="风险说明">{{ item.action.preview.risk }}</el-descriptions-item>
                  <el-descriptions-item label="审批截止">{{ item.action.expiresAt }}</el-descriptions-item>
                  <el-descriptions-item v-if="item.action.decidedBy" label="审批人">
                    {{ item.action.decidedBy }}（{{ item.action.decidedTime }}）
                  </el-descriptions-item>
                  <el-descriptions-item v-if="item.action.status === 'SUCCEEDED' && item.action.result" label="执行结果">
                    {{ JSON.stringify(item.action.result) }}
                  </el-descriptions-item>
                  <el-descriptions-item v-if="item.action.error" label="失败原因">{{ item.action.error }}</el-descriptions-item>
                </el-descriptions>
                <div v-if="canDecide(item.action)" class="approval-actions">
                  <el-button type="primary" :loading="item.busy === 'approve'" :disabled="!!item.busy || running" @click="decide(item, 'approve')">
                    {{ item.action.status === 'APPROVED' ? '重试执行' : '批准' }}
                  </el-button>
                  <el-button v-if="item.action.status === 'PENDING'" :loading="item.busy === 'reject'" :disabled="!!item.busy || running" @click="decide(item, 'reject')">
                    拒绝
                  </el-button>
                  <span v-if="item.action.expired" class="approval-tip">已超过审批截止时间，提交后会按过期处理</span>
                </div>
              </div>

              <!-- 错误提示 -->
              <el-alert v-else-if="item.kind === 'error'" :title="item.text" :type="item.type" show-icon :closable="false" class="error-alert" />
            </div>
          </el-scrollbar>

          <div class="input-area">
            <el-input
              v-model="input"
              type="textarea"
              :rows="3"
              maxlength="2000"
              show-word-limit
              resize="none"
              placeholder="例如：最近 7 天登录失败最多的账号是谁？（Ctrl + Enter 发送）"
              :disabled="running"
              @keydown.ctrl.enter="send"
            />
            <el-button type="primary" class="send-button" :loading="running" :disabled="!input.trim()" @click="send">发送</el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup name="AgentChat">
import { nextTick, onMounted, ref } from 'vue'
import {
  approveAction,
  getAction,
  getMessages,
  listConversation,
  rejectAction,
  sendMessage,
  streamChat,
  streamDecide
} from '@/api/agent/chat'

const conversations = ref([])
const conversationId = ref(null)
const items = ref([])
const input = ref('')
const running = ref(false)
const useStream = ref(true)
const scrollRef = ref(null)

let keySeed = 0
function nextKey() {
  keySeed += 1
  return keySeed
}

// 状态只来自服务端 agent_action，不从模型回复里解析
const STATUS = {
  PENDING: { label: '待审批', type: 'warning' },
  APPROVED: { label: '已批准，执行未完成', type: 'warning' },
  REJECTED: { label: '已拒绝', type: 'info' },
  EXPIRED: { label: '已过期', type: 'info' },
  EXECUTING: { label: '执行结果未知，需人工确认', type: 'danger' },
  SUCCEEDED: { label: '已执行成功', type: 'success' },
  FAILED: { label: '执行失败', type: 'danger' }
}

function statusOf(action) {
  if (action.status === 'PENDING' && action.expired) {
    return { label: '待审批（已过期）', type: 'info' }
  }
  return STATUS[action.status] || { label: action.status, type: 'info' }
}

function canDecide(action) {
  // APPROVED 表示上次批准后恢复执行失败：允许重试，服务端用条件更新保证写操作最多执行一次
  return action.status === 'PENDING' || action.status === 'APPROVED'
}

function targetText(action) {
  const t = (action.preview && action.preview.target) || {}
  const parts = [t.userName]
  if (t.nickName) {
    parts.push('昵称：' + t.nickName)
  }
  if (t.deptName) {
    parts.push('部门：' + t.deptName)
  }
  return parts.filter(Boolean).join('，')
}

function scrollToBottom() {
  nextTick(() => {
    const wrap = scrollRef.value && scrollRef.value.wrapRef
    if (wrap) {
      wrap.scrollTop = wrap.scrollHeight
    }
  })
}

function push(item) {
  items.value.push({ key: nextKey(), ...item })
  scrollToBottom()
  return items.value[items.value.length - 1]
}

// type 为 warning 时表示"操作本身已有结果（以审批卡片为准），只是回复没生成"
function pushError(text, type) {
  push({ kind: 'error', text: text || '操作失败', type: type || 'error' })
}

// 审批运行出错时服务端附带的 action：已到终态说明写操作已经处理完，只是模型回复失败
const SETTLED = ['SUCCEEDED', 'FAILED', 'REJECTED', 'EXPIRED', 'EXECUTING']

// 知识库状态由服务端按本轮检索结果计算（knowledge.status），不从模型回复里判断
const KNOWLEDGE_STATUS = {
  not_found: { label: '知识库中未找到依据', type: 'warning' },
  uncited: { label: '回答未引用知识库来源', type: 'info' },
  unavailable: { label: '知识库暂不可用，回答未使用知识库', type: 'warning' }
}

function pushKnowledge(knowledge) {
  if (knowledge && knowledge.status) {
    push({ kind: 'sources', knowledge: knowledge })
  }
}

// 审批结果的可信字段（status）由服务端生成，可以作为步骤状态显示
const OUTCOME_TEXT = {
  SUCCEEDED: '已执行成功',
  REJECTED: '已拒绝',
  EXPIRED: '已过期',
  FAILED: '执行失败',
  EXECUTING: '结果未知',
  PENDING: '未批准',
  APPROVED: '未完成'
}

function toolResultItem(m) {
  let parsed = null
  try {
    parsed = JSON.parse(m.content)
  } catch (e) {
    parsed = null
  }
  const ok = parsed ? parsed.ok === true : m.status !== 'error'
  return {
    kind: 'tool_result',
    name: m.name,
    ok: ok,
    statusText: parsed && parsed.status ? OUTCOME_TEXT[parsed.status] || parsed.status : '',
    error: parsed && !ok ? parsed.message || parsed.error || '' : '',
    content: m.content,
    expanded: false
  }
}

// 把服务端返回的消息（human / ai / tool）转成页面条目
function appendMessages(messages) {
  for (const m of messages || []) {
    if (m.type === 'human') {
      push({ kind: 'user', text: m.content })
    } else if (m.type === 'ai') {
      if (m.content) {
        push({ kind: 'assistant', text: m.content })
        pushKnowledge(m.knowledge)
      }
      for (const c of m.toolCalls || []) {
        push({ kind: 'tool_call', name: c.name, args: JSON.stringify(c.args) })
      }
    } else if (m.type === 'tool') {
      push(toolResultItem(m))
    }
  }
}

// 新增或更新审批卡片：同一个 actionId 只有一张卡片，内容整体替换为服务端最新数据
function upsertApproval(action) {
  if (!action) {
    return
  }
  const card = items.value.find(i => i.kind === 'approval' && i.action.actionId === action.actionId)
  if (card) {
    card.action = action
    // 卡片原地更新后会变高（多了审批人、执行结果），把下面的模型总结顶出可视区，所以重新滚到底部
    scrollToBottom()
  } else {
    push({ kind: 'approval', action: action, busy: '' })
  }
}

function applyResult(data) {
  if (data.conversationId) {
    conversationId.value = data.conversationId
  }
  upsertApproval(data.action)
  upsertApproval(data.pendingAction)
}

async function loadConversations() {
  try {
    const res = await listConversation({ pageNum: 1, pageSize: 50 })
    conversations.value = res.rows || []
  } catch (e) {
    // request.js 已经提示过错误
  }
}

function newConversation() {
  conversationId.value = null
  items.value = []
}

async function openConversation(id) {
  if (running.value) {
    return
  }
  try {
    const res = await getMessages(id)
    conversationId.value = id
    items.value = []
    appendMessages(res.data)
    upsertApproval(res.pendingAction)
  } catch (e) {
    // request.js 已经提示过错误
  }
}

// ---------------------------------------------------------------------------
// 流式事件处理
// ---------------------------------------------------------------------------

function createStreamHandler() {
  let current = null // 正在接收 token 的模型回复条目
  let lastAssistant = null // 本次运行最后一条模型回复，final 时用服务端的最终文本覆盖（以服务端保存的回复为准）
  return function onEvent(event, data) {
    if (event === 'token') {
      if (!current) {
        current = push({ kind: 'assistant', text: '', streaming: true })
        lastAssistant = current
      }
      current.text += data.text
      scrollToBottom()
    } else if (event === 'step') {
      // settle：新消息之前把已过期的审批收尾，附带该操作的最新状态（以服务端为准刷新卡片）
      upsertApproval(data.action)
      for (const m of data.messages || []) {
        if (m.type === 'ai') {
          // 节点完成时以完整消息为准，替换掉逐字拼接的文本
          if (current) {
            current.text = m.content || current.text
            current.streaming = false
            if (!current.text) {
              items.value = items.value.filter(i => i !== current)
            }
            current = null
          } else if (m.content) {
            lastAssistant = push({ kind: 'assistant', text: m.content })
          }
          for (const c of m.toolCalls || []) {
            push({ kind: 'tool_call', name: c.name, args: JSON.stringify(c.args) })
          }
        } else if (m.type === 'tool') {
          push(toolResultItem(m))
        }
      }
    } else if (event === 'approval') {
      upsertApproval(data.pendingAction)
    } else if (event === 'final') {
      if (current) {
        current.streaming = false
        current = null
      }
      // 流式过程中的消息不带引用信息；最终回复的文本和来源以 final 里服务端生成的为准
      if (lastAssistant && data.reply) {
        lastAssistant.text = data.reply
      }
      pushKnowledge(data.knowledgeStatus ? {
        status: data.knowledgeStatus,
        sources: data.sources,
        references: data.references,
        invalidCitations: data.invalidCitations,
        suspiciousCitations: data.suspiciousCitations
      } : null)
      applyResult(data)
    } else if (event === 'error') {
      if (current) {
        current.streaming = false
        current = null
      }
      // 错误文案和卡片状态都以服务端 agent_action 为准：执行成功后模型总结失败时，
      // 卡片显示"已执行成功"，提示"操作已执行成功，但回复生成失败"，不会出现重试按钮
      upsertApproval(data.action)
      pushError(data.msg, data.action && SETTLED.includes(data.action.status) ? 'warning' : 'error')
    }
  }
}

// ---------------------------------------------------------------------------
// 发送消息
// ---------------------------------------------------------------------------

async function send() {
  const text = input.value.trim()
  if (!text || running.value) {
    return
  }
  running.value = true
  push({ kind: 'user', text: text })
  input.value = ''
  const body = { message: text }
  if (conversationId.value) {
    body.conversationId = conversationId.value
  }
  try {
    if (useStream.value) {
      await streamChat(body, createStreamHandler())
    } else {
      const res = await sendMessage(body)
      // 普通请求返回本次新增的全部消息。刚才的用户消息已经显示过了，跳过它；
      // 它之前可能还有"过期审批收尾"产生的消息，不能简单地丢掉第一条
      const messages = res.data.messages || []
      const index = messages.findIndex(m => m.type === 'human' && m.content === text)
      appendMessages(index >= 0 ? messages.filter((m, i) => i !== index) : messages)
      applyResult(res.data)
    }
  } catch (e) {
    pushError(e && e.message ? e.message : String(e))
  } finally {
    running.value = false
    loadConversations()
  }
}

// ---------------------------------------------------------------------------
// 批准 / 拒绝：按钮在请求期间禁用，防止重复点击；失败后重新读取服务端状态，
// 仍是 PENDING 或 APPROVED 时允许重试（服务端保证写操作最多执行一次）
// ---------------------------------------------------------------------------

async function decide(card, decision) {
  if (card.busy || running.value) {
    return
  }
  card.busy = decision
  running.value = true
  try {
    if (useStream.value) {
      await streamDecide(card.action.actionId, decision, createStreamHandler())
    } else {
      const res = await (decision === 'approve' ? approveAction : rejectAction)(card.action.actionId)
      appendMessages(res.data.messages)
      applyResult(res.data)
      if (res.data.replyError) {
        // 操作已到终态（状态见卡片），只是之后的模型回复生成失败
        pushError(res.data.replyError, 'warning')
      }
    }
  } catch (e) {
    pushError(e && e.message ? e.message : String(e))
  } finally {
    // 无论成功与否，都以服务端 agent_action 的最新状态为准刷新卡片
    try {
      const res = await getAction(card.action.actionId)
      upsertApproval(res.data)
    } catch (e) {
      // 读取失败时保留原状态
    }
    card.busy = ''
    running.value = false
    loadConversations()
  }
}

onMounted(() => {
  loadConversations()
})
</script>

<style scoped lang="scss">
.agent-chat {
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .conversation-item {
    padding: 8px 10px;
    border-radius: 4px;
    cursor: pointer;
    margin-bottom: 4px;
    &:hover {
      background: var(--el-fill-color-light);
    }
    &.active {
      background: var(--el-color-primary-light-9);
    }
  }
  .conversation-title {
    font-size: 14px;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .conversation-time {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
  .message-area {
    padding-right: 8px;
  }
  .message-row {
    margin: 10px 0;
    display: flex;
    &.row-user {
      justify-content: flex-end;
    }
  }
  .bubble {
    max-width: 80%;
    padding: 8px 12px;
    border-radius: 6px;
    line-height: 1.6;
    /* 纯文本显示：保留换行，不解析任何标记 */
    white-space: pre-wrap;
    word-break: break-word;
  }
  .bubble-user {
    background: var(--el-color-primary-light-8);
  }
  .bubble-assistant {
    background: var(--el-fill-color-light);
  }
  .cursor {
    color: var(--el-color-primary);
  }
  .step {
    font-size: 13px;
    color: var(--el-text-color-regular);
    width: 100%;
    .step-name {
      margin: 0 6px;
      font-weight: 600;
    }
    .step-args,
    .step-status {
      color: var(--el-text-color-secondary);
      word-break: break-all;
    }
    .step-error {
      color: var(--el-color-danger);
    }
  }
  .step-detail {
    margin: 6px 0 0;
    padding: 8px;
    max-height: 240px;
    overflow: auto;
    background: var(--el-fill-color-lighter);
    border-radius: 4px;
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .sources {
    max-width: 80%;
    font-size: 12px;
    color: var(--el-text-color-secondary);
    .sources-title {
      margin-bottom: 2px;
    }
    .source-item {
      word-break: break-all;
    }
    .source-id {
      margin-left: 4px;
      color: var(--el-text-color-placeholder);
    }
    .source-warning {
      margin-top: 4px;
      color: var(--el-color-warning);
    }
    .source-note {
      margin-top: 2px;
      color: var(--el-text-color-placeholder);
    }
    .references-title {
      margin-top: 6px;
      color: var(--el-text-color-placeholder);
    }
    .reference-item {
      color: var(--el-text-color-placeholder);
      word-break: break-all;
    }
  }
  .approval-card {
    width: 100%;
    max-width: 640px;
    border: 1px solid var(--el-color-warning-light-5);
    border-radius: 6px;
    padding: 12px;
    background: var(--el-bg-color);
  }
  .approval-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .approval-title {
    font-weight: 600;
  }
  .approval-actions {
    margin-top: 10px;
  }
  .approval-tip {
    margin-left: 10px;
    font-size: 12px;
    color: var(--el-color-warning);
  }
  .error-alert {
    width: 100%;
  }
  .input-area {
    display: flex;
    align-items: flex-end;
    gap: 10px;
    margin-top: 12px;
  }
  .send-button {
    height: 40px;
  }
}
</style>
