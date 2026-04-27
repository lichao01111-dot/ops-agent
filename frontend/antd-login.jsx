
// antd-login.jsx — Login + 2FA page using Ant Design v5

const AntdLoginPage = ({ onLogin }) => {
  const { Form, Input, Button, Card, Typography, Space, Divider, message } = antd;
  const { LockOutlined, UserOutlined, SafetyCertificateOutlined, ThunderboltOutlined } = icons;
  const { Title, Text } = Typography;

  const [step, setStep] = React.useState('login'); // 'login' | 'mfa'
  const [loading, setLoading] = React.useState(false);
  const [otp, setOtp] = React.useState(['', '', '', '', '', '']);
  const otpRefs = React.useRef([]);
  const [form] = Form.useForm();
  const [messageApi, contextHolder] = message.useMessage();

  const handleLogin = (values) => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      setStep('mfa');
    }, 1100);
  };

  const handleMfa = () => {
    const code = otp.join('');
    if (code.length < 6) { messageApi.warning('请输入完整的6位验证码'); return; }
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
      onLogin({ username: form.getFieldValue('username') });
    }, 900);
  };

  const handleOtpChange = (val, idx) => {
    if (!/^\d*$/.test(val)) return;
    const next = [...otp];
    next[idx] = val.slice(-1);
    setOtp(next);
    if (val && idx < 5) otpRefs.current[idx + 1]?.focus();
  };

  const handleOtpKeyDown = (e, idx) => {
    if (e.key === 'Backspace' && !otp[idx] && idx > 0) otpRefs.current[idx - 1]?.focus();
    if (e.key === 'Enter') handleMfa();
  };

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'linear-gradient(135deg, #f0f4ff 0%, #e8f0fe 50%, #f5f3ff 100%)',
      position: 'relative', overflow: 'hidden'
    }}>
      {contextHolder}

      {/* Decorative circles */}
      <div style={{ position:'absolute', top:-80, right:-80, width:320, height:320, borderRadius:'50%', background:'rgba(29,78,216,0.06)', pointerEvents:'none' }}/>
      <div style={{ position:'absolute', bottom:-60, left:-60, width:240, height:240, borderRadius:'50%', background:'rgba(99,102,241,0.06)', pointerEvents:'none' }}/>

      <div style={{ width: 420, zIndex: 1 }}>
        {/* Header */}
        <div style={{ textAlign:'center', marginBottom: 28 }}>
          <div style={{
            display:'inline-flex', alignItems:'center', justifyContent:'center',
            width:52, height:52, borderRadius:14, marginBottom:14,
            background:'linear-gradient(135deg,#1d4ed8,#3b82f6)',
            boxShadow:'0 8px 24px rgba(29,78,216,0.3)'
          }}>
            <ThunderboltOutlined style={{ fontSize:24, color:'white' }}/>
          </div>
          <Title level={3} style={{ margin:0, color:'#0f172a', letterSpacing:'-0.02em' }}>JARVIS</Title>
          <Text type="secondary" style={{ fontSize:12, letterSpacing:'0.06em', textTransform:'uppercase' }}>
            Enterprise AI Agent Platform
          </Text>
        </div>

        <Card
          bordered={false}
          style={{ borderRadius:16, boxShadow:'0 4px 40px rgba(0,0,0,0.08)', border:'1px solid rgba(29,78,216,0.1)' }}
          bodyStyle={{ padding:'32px 32px 24px' }}
        >
          {/* Bank badge */}
          <div style={{
            display:'flex', alignItems:'center', gap:8, padding:'8px 12px',
            background:'rgba(29,78,216,0.06)', borderRadius:8, marginBottom:24,
            border:'1px solid rgba(29,78,216,0.12)'
          }}>
            <div style={{ width:8, height:8, borderRadius:'50%', background:'#1d4ed8' }}/>
            <Text style={{ fontSize:12, color:'#1d4ed8', fontWeight:500 }}>
              建设银行股份有限公司 · Construction Bank Corp.
            </Text>
          </div>

          {step === 'login' ? (
            <>
              <Title level={5} style={{ marginBottom:20, color:'#0f172a' }}>登录 / Sign In</Title>
              <Form form={form} layout="vertical" onFinish={handleLogin} requiredMark={false}>
                <Form.Item
                  name="username"
                  label={<Text style={{ fontSize:12, fontWeight:500, color:'#475569' }}>用户名 / Username</Text>}
                  rules={[{ required:true, message:'请输入用户名' }]}
                >
                  <Input
                    prefix={<UserOutlined style={{ color:'#94a3b8' }}/>}
                    placeholder="请输入工号或用户名"
                    size="large"
                    style={{ borderRadius:8 }}
                  />
                </Form.Item>
                <Form.Item
                  name="password"
                  label={<Text style={{ fontSize:12, fontWeight:500, color:'#475569' }}>密码 / Password</Text>}
                  rules={[{ required:true, message:'请输入密码' }]}
                >
                  <Input.Password
                    prefix={<LockOutlined style={{ color:'#94a3b8' }}/>}
                    placeholder="请输入密码"
                    size="large"
                    style={{ borderRadius:8 }}
                  />
                </Form.Item>
                <Form.Item style={{ marginBottom:0, marginTop:8 }}>
                  <Button
                    type="primary" htmlType="submit" block size="large"
                    loading={loading} style={{ borderRadius:8, height:44, fontWeight:600 }}
                  >
                    {loading ? '验证中...' : '登录 / Sign In'}
                  </Button>
                </Form.Item>
              </Form>
            </>
          ) : (
            <>
              <div style={{ textAlign:'center', marginBottom:24 }}>
                <div style={{
                  width:48, height:48, borderRadius:12, background:'rgba(29,78,216,0.08)',
                  display:'flex', alignItems:'center', justifyContent:'center', margin:'0 auto 12px'
                }}>
                  <SafetyCertificateOutlined style={{ fontSize:22, color:'#1d4ed8' }}/>
                </div>
                <Title level={5} style={{ margin:0, color:'#0f172a' }}>双因素验证 / 2FA</Title>
                <Text type="secondary" style={{ fontSize:13 }}>请输入动态令牌 / Enter your OTP</Text>
              </div>

              <div style={{ display:'flex', gap:8, justifyContent:'center', marginBottom:24 }}>
                {otp.map((v, i) => (
                  <input
                    key={i}
                    ref={el => otpRefs.current[i] = el}
                    value={v}
                    onChange={e => handleOtpChange(e.target.value, i)}
                    onKeyDown={e => handleOtpKeyDown(e, i)}
                    maxLength={1}
                    style={{
                      width:44, height:52, textAlign:'center', fontSize:22, fontWeight:700,
                      border:'1px solid #e2e8f0', borderRadius:8, outline:'none',
                      color:'#0f172a', fontFamily:'monospace',
                      transition:'border-color 0.2s', background:'#fafafa'
                    }}
                    onFocus={e => e.target.style.borderColor = '#1d4ed8'}
                    onBlur={e => e.target.style.borderColor = '#e2e8f0'}
                  />
                ))}
              </div>

              <Space direction="vertical" style={{ width:'100%' }} size={8}>
                <Button
                  type="primary" block size="large" loading={loading}
                  onClick={handleMfa} style={{ borderRadius:8, height:44, fontWeight:600 }}
                >
                  确认登录 / Confirm
                </Button>
                <Button block size="large" onClick={() => { setStep('login'); setOtp(['','','','','','']); }}
                  style={{ borderRadius:8 }}>
                  ← 返回 / Back
                </Button>
              </Space>
            </>
          )}

          <Divider style={{ margin:'20px 0 12px' }}/>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
            <Text type="secondary" style={{ fontSize:11 }}>© 2026 JARVIS Platform v2.4</Text>
            <Space size={12}>
              <a href="#" style={{ color:'#94a3b8', fontSize:11 }}>帮助</a>
              <a href="#" style={{ color:'#94a3b8', fontSize:11 }}>Privacy</a>
            </Space>
          </div>
        </Card>
      </div>
    </div>
  );
};

Object.assign(window, { AntdLoginPage });
