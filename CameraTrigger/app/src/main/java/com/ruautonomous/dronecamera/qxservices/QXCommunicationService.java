package com.ruautonomous.dronecamera.qxservices;

import android.app.Service;
import android.content.Intent;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkInfo;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Message;
import android.os.Messenger;
import android.os.RemoteException;
import android.util.Log;

import java.io.IOException;
import java.lang.ref.WeakReference;
import java.net.ConnectException;

public class QXCommunicationService extends Service {


    public static final String  TAG = "qxservice";
    public static final int SEARCHQX =1;
    public static final int TRIGGERQX=2;
    public static final int STATUSQX=3;
    public static final int REGISTER = 4;
    public static  QXHandler qx;
    private Messenger client;
    private PictureStorageServer pictureStorageServer;







    public void  onCreate(){
        super.onCreate();


    }

    final Messenger mMessenger = new Messenger(new IncomingHandler(this));


    public void setInterfaceWIFI(){

        ConnectivityManager connectivityManager = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        Network etherNetwork = null;

        for (Network network : connectivityManager.getAllNetworks()) {
            NetworkInfo networkInfo = connectivityManager.getNetworkInfo(network);
            if (networkInfo.getType() == ConnectivityManager.TYPE_WIFI) {
                etherNetwork = network;

            }
        }
        Network boundNetwork = connectivityManager.getBoundNetworkForProcess();
        if (boundNetwork != null) {
            NetworkInfo boundNetworkInfo = connectivityManager.getNetworkInfo(boundNetwork);
            if (boundNetworkInfo.getType() != ConnectivityManager.TYPE_WIFI) {
                if (etherNetwork != null) {
                    connectivityManager.bindProcessToNetwork(etherNetwork);

                }
            }
        }
        if(etherNetwork!=null)
            connectivityManager.bindProcessToNetwork(etherNetwork);
    }

    @Override
    public IBinder onBind(Intent intent) {

        setInterfaceWIFI();
        return mMessenger.getBinder();
    }


    private void serviceSearchQX(){
        if(qx==null) return;

        try{
            qx.searchQx(getApplicationContext());

        }
        catch (ConnectException e){
            Log.e(TAG,"failed");
        }

        Bundle data = new Bundle();
        data.putBoolean("status",qx.status());
        send(QxCommunicationResponseClient.QXSEARCHUPDATE,null,data);

    }




    public void serviceTriggerQX(){


            new Thread(new Runnable() {
                @Override
                public void run() {
                    qx.capture();

                }
            }).start();

    }


    private boolean serviceStatusQX(){
        return qx.status();
    }


    public void send(int message,Messenger replyTo,Bundle data){
        if(client!=null){
            Message msg = Message.obtain(null,message,0,0);
            if(replyTo!=null)
                msg.replyTo = replyTo;
            if(data!=null)
                msg.setData(data);

            try {
                client.send(msg);
            } catch (RemoteException e) {
                e.printStackTrace();
            }
        }

    }



    private static class  IncomingHandler extends Handler{

        private WeakReference<QXCommunicationService> serviceWeakReference;

        IncomingHandler(QXCommunicationService service){
            serviceWeakReference =new WeakReference<>(service);
        }

        @Override
        public void handleMessage(Message msg){
            QXCommunicationService service = serviceWeakReference.get();
            switch(msg.what){

                case SEARCHQX:
                    service.serviceSearchQX();
                    break;
                case TRIGGERQX:
                    service.serviceTriggerQX();
                    break;
                case STATUSQX:
                    boolean status = service.serviceStatusQX();


                    Bundle data = new Bundle();
                    data.putBoolean("status",status);
                    service.send(QxCommunicationResponseClient.QXSTATUS,null,data);

                    break;
                case REGISTER:
                    service.client = msg.replyTo;
                    try {
                        service.pictureStorageServer = new PictureStorageServer(service.client,service.getApplicationContext());
                        service.qx = new QXHandler(service.pictureStorageServer);

                    }
                    catch (IOException e){
                        Log.e(TAG,e.toString()) ;
                    }
                    break;
                default:
                    super.handleMessage(msg);

            }
        }
    }

}