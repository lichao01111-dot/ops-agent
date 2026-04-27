
// antd-sidebar.jsx — Sidebar with agent switcher, conversation history

const ANTD_AGENTS = [
  { id: 'it-ops',  name: 'IT运维助手',  nameEn: 'IT Ops Agent',      role: 'IT Operations',      icon: '💻', color: '#1d4ed8' },
  { id: 'risk',    name: '风控顾问',    nameEn: 'Risk Advisor',       role: 'Risk & Compliance',  icon: '🛡️', color: '#d97706' },
  { id: 'finance', name: '财务分析师',  nameEn: 'Finance Analyst',    role: 'Financial Analysis', icon: '📊', color: '#059669' },
  { id: 'service', name: '客服专员',    nameEn: 'Customer Service',   role: 'Customer Support',   icon: '🎧', color: '#7c3aed' },
];

const ANTD_CONVERSATIONS = [
  { id:'c1', title:'服务器集群健康检查',   time:'今天', agent:'it-ops',  preview:'已完成3个节点扫描' },
  { id:'c2', title:'数据库备份状态报告',   time:'今天', agent:'it-ops',  preview:'备份任务全部完成' },
  { id:'c3', title:'网络带宽异常排查',     time:'今天', agent:'risk',    preview:'发现2处异常流量' },
  { id:'c4', title:'月度运维分析报告',     time:'昨天', agent:'it-ops',  preview:'可用率达99.97%' },
  { id:'c5', title:'Q1风险评估报告',       time:'昨天', agent:'risk',    preview:'低风险，建议持续监控' },
  { id:'c6', title:'应用部署流水线检查',   time:'昨天', agent:'it-ops',  preview:'3个服务待更新' },
  { id:'c7', title:'防火墙规则审计',       time:'更早', agent:'risk',    preview:'发现5条冗余规则' },
  { id:'c8', title:'K8S集群资源分析',      time:'更早', agent:'it-ops',  preview:'推荐扩容2个节点' },
];

const AntdSidebar = ({ currentAgent, onAgentChange, currentConv, onConvChange, onNewChat, user, collapsed, onCollapse }) => {
  const { Layout, Button, Input, Avatar, Badge, Typography, Tooltip, Divider, Tag } = antd;
  const {
    PlusOutlined, SearchOutlined, MessageOutlined, LeftOutlined, RightOutlined,
    UserOutlined, SettingOutlined, LogoutOutlined
  } = icons;
  const { Sider } = Layout;
  const { Text } = Typography;

  const [search, setSearch] = React.useState('');

  const grouped = React.useMemo(() => {
    const filtered = ANTD_CONVERSATIONS.filter(c => c.title.includes(search));
    const groups = { '今天 Today': [], '昨天 Yesterday': [], '更早 Earlier': [] };
    filtered.forEach(c => {
      if (c.time === '今天') groups['今天 Today'].push(c);
      else if (c.time === '昨天') groups['昨天 Yesterday'].push(c);
      else groups['更早 Earlier'].push(c);
    });
    return groups;
  }, [search]);

  return (
    <Sider
      width={260} collapsedWidth={64} collapsed={collapsed}
      style={{ background:'#fff', borderRight:'1px solid #f0f0f0', overflow:'hidden', height:'100%', display:'flex', flexDirection:'column' }}
      theme="light"
    >
      <div style={{ display:'flex', flexDirection:'column', height:'100%' }}>

        {/* Logo */}
        <div style={{ padding: collapsed ? '16px 16px' : '16px 16px 12px', borderBottom:'1px solid #f0f0f0' }}>
          <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom: collapsed ? 0 : 14 }}>
            <div style={{
              width:34, height:34, borderRadius:9, flexShrink:0,
              background:'linear-gradient(135deg,#1d4ed8,#3b82f6)',
              display:'flex', alignItems:'center', justifyContent:'center',
              boxShadow:'0 4px 12px rgba(29,78,216,0.3)'
            }}>
              <span style={{ fontSize:16 }}>⚡</span>
            </div>
            {!collapsed && (
              <div style={{ flex:1, overflow:'hidden' }}>
                <div style={{ fontWeight:700, fontSize:15, color:'#0f172a', letterSpacing:'-0.02em' }}>JARVIS</div>
                <div style={{ fontSize:10, color:'#94a3b8', letterSpacing:'0.06em', textTransform:'uppercase' }}>AI Agent Platform</div>
              </div>
            )}
            <Button
              type="text" size="small"
              icon={collapsed ? <RightOutlined/> : <LeftOutlined/>}
              onClick={() => onCollapse(!collapsed)}
              style={{ color:'#94a3b8', flexShrink:0 }}
            />
          </div>

          {/* Agent Selector */}
          {!collapsed && (
            <div style={{ marginBottom:10 }}>
              <Text style={{ fontSize:10, color:'#94a3b8', fontWeight:600, letterSpacing:'0.08em', textTransform:'uppercase', display:'block', marginBottom:6 }}>
                当前 Agent
              </Text>
              {ANTD_AGENTS.map(a => (
                <div
                  key={a.id}
                  onClick={() => onAgentChange(a)}
                  style={{
                    display:'flex', alignItems:'center', gap:8, padding:'7px 8px', borderRadius:8,
                    cursor:'pointer', marginBottom:2, transition:'all 0.15s',
                    background: currentAgent.id === a.id ? `${a.color}12` : 'transparent',
                    border: currentAgent.id === a.id ? `1px solid ${a.color}30` : '1px solid transparent'
                  }}
                  onMouseEnter={e => { if(currentAgent.id!==a.id) e.currentTarget.style.background='#f8fafc'; }}
                  onMouseLeave={e => { if(currentAgent.id!==a.id) e.currentTarget.style.background='transparent'; }}
                >
                  <div style={{
                    width:26, height:26, borderRadius:7, flexShrink:0,
                    background:`${a.color}15`, border:`1px solid ${a.color}30`,
                    display:'flex', alignItems:'center', justifyContent:'center', fontSize:13
                  }}>{a.icon}</div>
                  <div style={{ flex:1, overflow:'hidden' }}>
                    <div style={{ fontSize:12, fontWeight:600, color: currentAgent.id===a.id ? a.color : '#334155', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{a.name}</div>
                    <div style={{ fontSize:10, color:'#94a3b8', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{a.role}</div>
                  </div>
                  {currentAgent.id === a.id && <Badge color={a.color} dot style={{ flexShrink:0 }}/>}
                </div>
              ))}
            </div>
          )}

          {collapsed ? (
            <Tooltip title="新建对话" placement="right">
              <Button type="primary" icon={<PlusOutlined/>} block style={{ borderRadius:8, marginTop:8 }} onClick={onNewChat}/>
            </Tooltip>
          ) : (
            <Button type="primary" icon={<PlusOutlined/>} block style={{ borderRadius:8 }} onClick={onNewChat}>
              新建对话 / New Chat
            </Button>
          )}
        </div>

        {/* Search */}
        {!collapsed && (
          <div style={{ padding:'10px 12px 4px' }}>
            <Input
              prefix={<SearchOutlined style={{ color:'#94a3b8', fontSize:12 }}/>}
              placeholder="搜索对话..."
              size="small"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ borderRadius:8, fontSize:12 }}
            />
          </div>
        )}

        {/* Conversation List */}
        <div style={{ flex:1, overflowY:'auto', padding:'4px 8px' }}>
          {!collapsed ? Object.entries(grouped).map(([label, convs]) => convs.length > 0 && (
            <div key={label}>
              <div style={{ fontSize:10, color:'#94a3b8', fontWeight:600, letterSpacing:'0.07em', textTransform:'uppercase', padding:'8px 6px 4px' }}>{label}</div>
              {convs.map(c => {
                const agent = ANTD_AGENTS.find(a => a.id === c.agent);
                return (
                  <div
                    key={c.id}
                    onClick={() => onConvChange(c)}
                    style={{
                      padding:'8px 10px', borderRadius:8, cursor:'pointer', marginBottom:1,
                      background: currentConv?.id===c.id ? '#f0f4ff' : 'transparent',
                      border: currentConv?.id===c.id ? '1px solid #dde5ff' : '1px solid transparent',
                      transition:'all 0.12s'
                    }}
                    onMouseEnter={e => { if(currentConv?.id!==c.id) e.currentTarget.style.background='#f8fafc'; }}
                    onMouseLeave={e => { if(currentConv?.id!==c.id) e.currentTarget.style.background='transparent'; }}
                  >
                    <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                      <div style={{ width:5, height:5, borderRadius:'50%', background: agent?.color || '#94a3b8', flexShrink:0 }}/>
                      <Text style={{ fontSize:12, fontWeight: currentConv?.id===c.id ? 500 : 400, color: currentConv?.id===c.id ? '#1d4ed8' : '#334155', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis', flex:1 }}>{c.title}</Text>
                    </div>
                    <div style={{ fontSize:10, color:'#94a3b8', marginLeft:11, marginTop:1, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{c.preview}</div>
                  </div>
                );
              })}
            </div>
          )) : (
            <div style={{ padding:'8px 4px', display:'flex', flexDirection:'column', gap:4 }}>
              {ANTD_CONVERSATIONS.slice(0,5).map(c => {
                const agent = ANTD_AGENTS.find(a => a.id === c.agent);
                return (
                  <Tooltip key={c.id} title={c.title} placement="right">
                    <div onClick={() => onConvChange(c)} style={{
                      width:36, height:36, borderRadius:8, display:'flex', alignItems:'center', justifyContent:'center',
                      background: currentConv?.id===c.id ? '#f0f4ff' : '#f8fafc', cursor:'pointer',
                      fontSize:11, color:'#64748b', fontWeight:600
                    }}>
                      <MessageOutlined style={{ fontSize:14, color: agent?.color || '#94a3b8' }}/>
                    </div>
                  </Tooltip>
                );
              })}
            </div>
          )}
        </div>

        {/* User */}
        <div style={{ padding: collapsed ? '10px 14px' : '12px 14px', borderTop:'1px solid #f0f0f0', display:'flex', alignItems:'center', gap:10 }}>
          <Avatar size={32} style={{ background:'linear-gradient(135deg,#1d4ed8,#3b82f6)', flexShrink:0, fontSize:13, fontWeight:700 }}>
            {user?.username?.charAt(0).toUpperCase() || 'U'}
          </Avatar>
          {!collapsed && (
            <>
              <div style={{ flex:1, overflow:'hidden' }}>
                <div style={{ fontSize:12, fontWeight:600, color:'#0f172a', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{user?.username || 'Admin'}</div>
                <div style={{ fontSize:10, color:'#94a3b8' }}>IT部 · 系统管理员</div>
              </div>
              <Tooltip title="设置"><Button type="text" size="small" icon={<SettingOutlined/>} style={{ color:'#94a3b8' }}/></Tooltip>
            </>
          )}
        </div>
      </div>
    </Sider>
  );
};

Object.assign(window, { AntdSidebar, ANTD_AGENTS, ANTD_CONVERSATIONS });
