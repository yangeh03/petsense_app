// Expo 项目的 ESLint 配置，基于官方规则集。
// 文档：https://docs.expo.dev/guides/using-eslint/
import expoConfig from 'eslint-config-expo/flat.js';
import { defineConfig, globalIgnores } from 'eslint/config';

export default defineConfig([expoConfig, globalIgnores(['dist/', '.expo/'])]);
