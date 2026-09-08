import { INITIAL_USERS } from './masterData';
import { User } from '@/types';

// 保護者判定は quest_users.role ('role_adult'/'role_child') を唯一の判定基準とする。
// ★注意: これはクライアント側のUI上の配慮（隠しボタンを子どもに見せないため）にすぎず、
// セキュリティ境界ではない。バックエンドは現状どのuser_idでも自称できてしまうため、
// 本当のアクセス制御はバックエンド側で別途実装される必要がある。
export const isParentUser = (user: User) => user.role === 'role_adult';

// 承認・却下の記録名義に使う代表の親ユーザーを返す。誰が実際にボタンを押したかは
// 区別せず「親」として固定で記録する(要件5)。
export const getRepresentativeParent = (allUsers: User[]): User => {
    const adult = allUsers.find(u => u.role === 'role_adult');
    return adult || allUsers[0] || INITIAL_USERS[0];
};
