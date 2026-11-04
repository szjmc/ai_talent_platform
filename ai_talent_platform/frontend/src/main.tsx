import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import App from './App';
import { AuthProvider } from './store/AuthContext';
import './styles.css';

dayjs.locale('zh-cn');

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#315efb', colorInfo: '#315efb', colorSuccess: '#16a377', colorWarning: '#e99a22',
          colorError: '#e55252', colorText: '#1d2433', colorTextSecondary: '#667085', colorBorder: '#e5e9f0',
          colorBgLayout: '#f5f7fb', borderRadius: 8, borderRadiusLG: 12, fontSize: 14,
          fontFamily: "Inter, 'PingFang SC', 'Microsoft YaHei', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          boxShadowSecondary: '0 8px 32px rgba(30, 45, 80, .12)',
        },
        components: {
          Button: { controlHeight: 36, primaryShadow: '0 3px 8px rgba(49,94,251,.18)', fontWeight: 500 },
          Card: { paddingLG: 22 },
          Table: { headerBg: '#f8f9fc', headerColor: '#606a7d', headerSplitColor: 'transparent', rowHoverBg: '#f7f9ff', cellPaddingBlock: 15 },
          Menu: { itemHeight: 44, itemBorderRadius: 7, itemSelectedBg: '#eaf0ff', itemSelectedColor: '#315efb', itemColor: '#536075', itemMarginInline: 12 },
          Input: { controlHeight: 38 }, Select: { controlHeight: 38 },
          Modal: { titleFontSize: 18 }, Tabs: { titleFontSize: 14 },
        },
      }}
    >
      <AntApp><BrowserRouter><AuthProvider><App /></AuthProvider></BrowserRouter></AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
