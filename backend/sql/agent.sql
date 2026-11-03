-- ----------------------------------------------------------------------------
-- 智能助手（Agent）新增的表和菜单。
-- 不修改若依原有表结构；本文件必须可以重复执行：
--   - 与若依脚本的"覆盖数据"语义一致：先 DROP 再 CREATE，重新导入会清空会话数据；
--     对应的 LangGraph checkpoint 文件由 scripts/init_db.py 手动执行时一并删除，避免库和 checkpoint 不一致；
--   - 菜单和授权用 ON DUPLICATE KEY UPDATE / INSERT IGNORE，重复执行结果一致。
-- 导入顺序：先 ry_20260417.sql 再本文件（若依脚本会 drop 重建 sys_menu、sys_role_menu，
-- 所以每次导入若依脚本后都要重新执行本文件，scripts/init_db.py 已按此顺序导入）。
-- ----------------------------------------------------------------------------

-- 会话表：conversation_id 同时作为 LangGraph checkpoint 的 thread_id。
-- 归属校验以本表的 user_id 为准，不信任前端传来的 conversation_id。
drop table if exists agent_conversation;
create table agent_conversation (
  conversation_id   varchar(64)     not null                   comment '会话ID（即 thread_id）',
  user_id           bigint(20)      not null                   comment '所属用户ID',
  title             varchar(100)    default ''                 comment '会话标题（取首条消息前若干字）',
  create_time       datetime                                   comment '创建时间',
  update_time       datetime                                   comment '最后活跃时间',
  primary key (conversation_id),
  key idx_agent_conv_user (user_id, update_time)
) engine=innodb comment = '智能助手会话表';

-- 菜单：一级菜单（C 类型，parent_id=0），由 sys_menu 动态加载，前端无需改路由。
-- 菜单 ID 从 2000 开始，避开若依自带菜单（最大 1060）。
insert into sys_menu (menu_id, menu_name, parent_id, order_num, path, component, query, route_name,
                      is_frame, is_cache, menu_type, visible, status, perms, icon, create_by, create_time, remark)
values (2000, '智能助手', 0, 5, 'agent', 'agent/chat/index', '', '', 1, 0, 'C', '0', '0',
        'agent:chat:use', 'message', 'admin', sysdate(), '后台智能助手菜单')
on duplicate key update menu_name = values(menu_name), parent_id = values(parent_id), order_num = values(order_num),
                        path = values(path), component = values(component), menu_type = values(menu_type),
                        perms = values(perms), icon = values(icon);

-- 授权给普通角色（role_id=2）；超级管理员不需要授权
insert ignore into sys_role_menu (role_id, menu_id) values (2, 2000);
