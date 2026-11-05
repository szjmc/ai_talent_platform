import { useState } from 'react';
import { App, Button, Checkbox, Form, Input } from 'antd';
import { ArrowRightOutlined, LockOutlined, NodeIndexOutlined, SafetyCertificateOutlined, RobotOutlined, BarChartOutlined, UserOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { Brand } from '../components/Common';
import { useAuth } from '../store/AuthContext';
import { formatApiError } from '../services/api';

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const { login, enterDemo } = useAuth(); const navigate = useNavigate(); const { message } = App.useApp();
  const onFinish = async (values: { username: string; password: string; remember: boolean }) => {
    setLoading(true);
    try { await login(values.username, values.password, values.remember); message.success('登录成功，欢迎回来'); navigate('/dashboard'); }
    catch (error) { message.error(formatApiError(error)); } finally { setLoading(false); }
  };
  return <div className="login-page">
    <section className="login-visual">
      <Brand light/>
      <div className="login-value"><div className="login-eyebrow">AI-NATIVE TALENT INTELLIGENCE</div><h1>让人才被看见，<br/>让组织更智慧。</h1><p>以 AI 为核心，连接人才全生命周期。用数据洞察组织，用智能驱动每一个人才决策。</p>
        <div className="login-feature-grid">
          <div className="login-feature"><i><RobotOutlined/></i>多智能体协同</div><div className="login-feature"><i><NodeIndexOutlined/></i>精准人岗匹配</div>
          <div className="login-feature"><i><BarChartOutlined/></i>自然语言数据洞察</div><div className="login-feature"><i><SafetyCertificateOutlined/></i>企业级安全合规</div>
        </div>
      </div>
      <div className="login-copyright">© 2026 智才云 TalentOS · 企业级 AI 数字化人才平台</div>
    </section>
    <section className="login-form-side"><div className="login-form-box"><h2>欢迎回来</h2><p>登录您的账号，开启智能人才管理之旅</p>
      <Form layout="vertical" onFinish={onFinish} requiredMark={false} initialValues={{ remember: true }}>
        <Form.Item label="账号" name="username" rules={[{ required: true, message: '请输入登录账号' }]}><Input prefix={<UserOutlined style={{ color: '#a0a8b7' }}/>} placeholder="请输入用户名" autoComplete="username"/></Form.Item>
        <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入登录密码' }]}><Input.Password prefix={<LockOutlined style={{ color: '#a0a8b7' }}/>} placeholder="请输入登录密码" autoComplete="current-password"/></Form.Item>
        <div className="login-options"><Form.Item name="remember" valuePropName="checked" noStyle><Checkbox>7 天内保持登录</Checkbox></Form.Item><a className="text-primary">忘记密码？</a></div>
        <Button type="primary" htmlType="submit" block loading={loading}>登录 <ArrowRightOutlined/></Button>
      </Form>
      <div className="login-demo-tip">尚未部署后端？<Button type="link" size="small" onClick={() => { enterDemo(); navigate('/dashboard'); }} style={{ padding: 0, height: 20 }}>进入产品演示</Button><br/>演示模式仅用于体验界面，不会修改任何业务数据。</div>
      <div className="login-footer">登录即代表您同意《服务协议》和《隐私政策》</div>
    </div></section>
  </div>;
}
