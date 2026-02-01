import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import { Platform } from 'react-native';
import { api } from './api'; // Ton client API existant

// Configuration des notifications
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

/**
 * Enregistre le device pour les push notifications
 * À appeler après le login ou au démarrage de l'app
 */
export async function registerForPushNotifications(): Promise<string | null> {
  // Les push ne fonctionnent que sur device physique
  if (!Device.isDevice) {
    console.log('⚠️ Push notifications nécessitent un appareil physique');
    return null;
  }

  try {
    // Vérifier/demander les permissions
    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;

    if (existingStatus !== 'granted') {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }

    if (finalStatus !== 'granted') {
      console.log('❌ Permission push refusée');
      return null;
    }

    // Config spécifique Android
    if (Platform.OS === 'android') {
      await Notifications.setNotificationChannelAsync('default', {
        name: 'default',
        importance: Notifications.AndroidImportance.MAX,
        vibrationPattern: [0, 250, 250, 250],
        lightColor: '#667eea',
      });
    }

    // Récupérer le token Expo
    const tokenData = await Notifications.getExpoPushTokenAsync({
      projectId: 'ton-project-id', // Remplace par ton projectId Expo
    });
    const token = tokenData.data;
    
    console.log('✅ Expo Push Token:', token);

    // Envoyer au backend
    await api.post('/users/register-push-token', {
      token: token,
      device_type: Platform.OS,
    });

    console.log('✅ Token enregistré sur le backend');
    return token;

  } catch (error) {
    console.error('❌ Erreur push notifications:', error);
    return null;
  }
}

/**
 * Listener pour les notifications reçues (app au premier plan)
 */
export function addNotificationReceivedListener(
  callback: (notification: Notifications.Notification) => void
) {
  return Notifications.addNotificationReceivedListener(callback);
}

/**
 * Listener pour les notifications cliquées
 */
export function addNotificationResponseListener(
  callback: (response: Notifications.NotificationResponse) => void
) {
  return Notifications.addNotificationResponseReceivedListener(callback);
}

/**
 * Hook pour gérer les notifications dans un composant
 */
export function useNotificationListeners(
  onReceived?: (notification: Notifications.Notification) => void,
  onClicked?: (data: any) => void
) {
  const notificationListener = Notifications.addNotificationReceivedListener(
    (notification) => {
      console.log('📬 Notification reçue:', notification);
      onReceived?.(notification);
    }
  );

  const responseListener = Notifications.addNotificationResponseReceivedListener(
    (response) => {
      const data = response.notification.request.content.data;
      console.log('👆 Notification cliquée:', data);
      onClicked?.(data);
    }
  );

  return () => {
    Notifications.removeNotificationSubscription(notificationListener);
    Notifications.removeNotificationSubscription(responseListener);
  };
}